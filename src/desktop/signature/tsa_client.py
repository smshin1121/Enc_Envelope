"""RFC 3161 TSA client for requesting and verifying timestamps.

Sends TimeStampReq (TSQ) to a TSA server and receives TimeStampResp (TSR).
Supports retry with exponential backoff and TST token verification.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests
from asn1crypto import algos, cms, core, tsp

from .exceptions import TSAError

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds

# A TSA on the loopback interface either answers immediately or is
# down — long timeouts and extra retries only delay the fallback.
_LOCAL_TIMEOUT_SECONDS = 2
_LOCAL_MAX_RETRIES = 2
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local_tsa(tsa_url: str) -> bool:
    """Return True if the TSA URL points at the loopback interface."""
    from urllib.parse import urlparse

    try:
        host = urlparse(tsa_url).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOCAL_HOSTS


def _build_tsq(data_hash: bytes) -> bytes:
    """Build an RFC 3161 TimeStampReq (TSQ) for a SHA-256 hash.

    Args:
        data_hash: SHA-256 hash of the data to timestamp (32 bytes).

    Returns:
        DER-encoded TSQ bytes.

    Raises:
        TSAError: If the hash length is invalid.
    """
    if len(data_hash) != 32:
        raise TSAError(
            f"Expected 32-byte SHA-256 hash, got {len(data_hash)} bytes"
        )

    message_imprint = tsp.MessageImprint({
        "hash_algorithm": algos.DigestAlgorithm({
            "algorithm": "sha256",
        }),
        "hashed_message": data_hash,
    })

    tsq = tsp.TimeStampReq({
        "version": "v1",
        "message_imprint": message_imprint,
        "cert_req": True,
    })

    return tsq.dump()


def _parse_tsr(tsr_bytes: bytes) -> bytes:
    """Parse a TimeStampResp and extract the TST token.

    Args:
        tsr_bytes: DER-encoded TSR bytes.

    Returns:
        DER-encoded TimeStampToken (ContentInfo) bytes.

    Raises:
        TSAError: If the TSR indicates failure or cannot be parsed.
    """
    try:
        tsr = tsp.TimeStampResp.load(tsr_bytes)
    except Exception as exc:
        raise TSAError(f"Failed to parse TSR: {exc}") from exc

    status = tsr["status"]["status"].native
    if status != "granted" and status != "granted_with_mods":
        fail_info = tsr["status"].get("fail_info")
        status_string = tsr["status"].get("status_string")
        raise TSAError(
            f"TSA request rejected: status={status}, "
            f"fail_info={fail_info}, status_string={status_string}"
        )

    tst_token = tsr["time_stamp_token"]
    if tst_token.native is None:
        raise TSAError("TSR contains no TimeStampToken")

    return tst_token.dump()


def request_timestamp(data_hash: bytes, tsa_url: str) -> bytes:
    """Send an RFC 3161 TSQ to a TSA and return the TST token.

    Retries with exponential backoff on network errors. Loopback TSA
    URLs use a shorter timeout (2s) and fewer retries (2) since a
    local server either responds immediately or is not running.

    Args:
        data_hash: SHA-256 hash (32 bytes) of the data to timestamp.
        tsa_url: URL of the TSA server endpoint.

    Returns:
        DER-encoded TST token bytes.

    Raises:
        TSAError: If the request fails after all retries.
    """
    if not tsa_url:
        raise TSAError("TSA URL is required")

    if _is_local_tsa(tsa_url):
        timeout_seconds = _LOCAL_TIMEOUT_SECONDS
        max_retries = _LOCAL_MAX_RETRIES
    else:
        timeout_seconds = _TIMEOUT_SECONDS
        max_retries = _MAX_RETRIES

    tsq_bytes = _build_tsq(data_hash)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "TSA request attempt %d/%d to %s",
                attempt, max_retries, tsa_url,
            )
            response = requests.post(
                tsa_url,
                data=tsq_bytes,
                headers={"Content-Type": "application/timestamp-query"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "application/timestamp-reply" not in content_type:
                logger.warning(
                    "Unexpected Content-Type from TSA: %s", content_type
                )

            tst_token = _parse_tsr(response.content)
            logger.info("TST token received successfully (attempt %d)", attempt)
            return tst_token

        except TSAError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                wait_time = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "TSA request attempt %d failed: %s. Retrying in %.1fs...",
                    attempt, exc, wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.error(
                    "TSA request failed after %d attempts", max_retries
                )

    raise TSAError(
        f"TSA request failed after {max_retries} attempts: {last_error}"
    )


def verify_timestamp(tst_token: bytes, tsa_cert_path: str) -> datetime:
    """Verify a TST token and return the genTime.

    Performs basic structural verification of the TST token and extracts
    the generation time. For full cryptographic verification, the TSA
    certificate chain should be validated separately.

    Args:
        tst_token: DER-encoded TST token (ContentInfo) bytes.
        tsa_cert_path: Path to the TSA certificate PEM file (for future
            full chain validation).

    Returns:
        The genTime from the TST token as a timezone-aware datetime.

    Raises:
        TSAError: If verification fails.
    """
    try:
        content_info = cms.ContentInfo.load(tst_token)
        if content_info["content_type"].native != "signed_data":
            raise TSAError(
                f"Expected signed_data, got {content_info['content_type'].native}"
            )

        signed_data = content_info["content"]
        encap_content = signed_data["encap_content_info"]

        if encap_content["content_type"].native != "tst_info":
            raise TSAError(
                f"Expected tst_info content, got {encap_content['content_type'].native}"
            )

        tst_info = tsp.TSTInfo.load(encap_content["content"].parsed.dump())
        gen_time = tst_info["gen_time"].native

        if gen_time is None:
            raise TSAError("TST token contains no genTime")

        # Ensure timezone-aware
        if gen_time.tzinfo is None:
            gen_time = gen_time.replace(tzinfo=timezone.utc)

        serial = tst_info["serial_number"].native
        logger.info(
            "TST verified: genTime=%s, serial=%s",
            gen_time.isoformat(), serial,
        )

        # TODO: Full cryptographic signature verification against tsa_cert_path
        # Currently performs structural validation only.

        return gen_time

    except TSAError:
        raise
    except Exception as exc:
        raise TSAError(f"Failed to verify TST token: {exc}") from exc
