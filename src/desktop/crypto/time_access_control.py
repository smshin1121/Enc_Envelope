"""Time-based access control using TSA (RFC 3161) verification."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .exceptions import AccessControlError
from .types import AccessCheckResult

logger = logging.getLogger(__name__)

_TSA_TIMEOUT_SECONDS = 10


def check_unlock_time(
    unlock_time_iso: str,
    tsa_url: Optional[str] = None,
) -> AccessCheckResult:
    """Check whether the current time has passed unlock_time.

    Uses dual verification:
    1. Local time as preliminary check
    2. TSA genTime as authoritative check (if TSA URL provided)

    Falls back to local time with warning if TSA is unavailable.

    Args:
        unlock_time_iso: The unlock time in ISO 8601 UTC format.
        tsa_url: Optional TSA server URL for RFC 3161 time verification.

    Returns:
        AccessCheckResult indicating whether access is allowed.

    Raises:
        AccessControlError: If unlock_time_iso is invalid.
    """
    unlock_time = _parse_iso_time(unlock_time_iso)
    local_now = datetime.now(tz=timezone.utc)

    # Step 1: Local time preliminary check
    if local_now < unlock_time:
        return AccessCheckResult(
            allowed=False,
            method="local_preliminary",
            current_time_iso=local_now.isoformat(),
            unlock_time_iso=unlock_time_iso,
            warning=None,
        )

    # Step 2: TSA verification (authoritative)
    if tsa_url:
        tsa_result = _verify_with_tsa(tsa_url, unlock_time)
        if tsa_result is not None:
            return tsa_result

        # TSA failed -- fall back to local with warning
        logger.warning("TSA verification failed, falling back to local time")
        return AccessCheckResult(
            allowed=True,
            method="local_fallback",
            current_time_iso=local_now.isoformat(),
            unlock_time_iso=unlock_time_iso,
            warning="TSA unavailable; verified with local time only",
        )

    # No TSA URL provided -- local only with warning
    return AccessCheckResult(
        allowed=True,
        method="local_fallback",
        current_time_iso=local_now.isoformat(),
        unlock_time_iso=unlock_time_iso,
        warning="No TSA URL configured; verified with local time only",
    )


def _parse_iso_time(iso_string: str) -> datetime:
    """Parse an ISO 8601 string to a timezone-aware datetime.

    Raises:
        AccessControlError: If parsing fails.
    """
    try:
        dt = datetime.fromisoformat(iso_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError) as exc:
        raise AccessControlError(
            f"Invalid ISO 8601 time: {iso_string}"
        ) from exc


def _verify_with_tsa(
    tsa_url: str,
    unlock_time: datetime,
) -> Optional[AccessCheckResult]:
    """Query TSA server and verify genTime >= unlock_time.

    Returns AccessCheckResult on success, None on TSA failure.
    """
    try:
        gen_time = _request_tsa_time(tsa_url)
        if gen_time is None:
            return None

        allowed = gen_time >= unlock_time
        return AccessCheckResult(
            allowed=allowed,
            method="tsa",
            current_time_iso=gen_time.isoformat(),
            unlock_time_iso=unlock_time.isoformat(),
            warning=None,
        )
    except Exception:
        logger.exception("TSA request failed")
        return None


def _request_tsa_time(tsa_url: str) -> Optional[datetime]:
    """Send RFC 3161 TimeStampReq and extract genTime.

    Returns the genTime as a datetime, or None on failure.
    """
    try:
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.x509 import ocsp  # noqa: F401 -- availability check
        import urllib.request

        # Build a minimal TSA request (RFC 3161)
        # Hash a nonce to create the message imprint
        nonce_data = os.urandom(16)
        digest = hashlib.sha256(nonce_data).digest()

        # Construct a basic DER-encoded TimeStampReq
        tsq = _build_timestamp_request(digest)

        req = urllib.request.Request(
            tsa_url,
            data=tsq,
            headers={"Content-Type": "application/timestamp-query"},
        )
        req.timeout = _TSA_TIMEOUT_SECONDS  # type: ignore[attr-defined]

        with urllib.request.urlopen(req, timeout=_TSA_TIMEOUT_SECONDS) as resp:
            tsr_data = resp.read()

        return _parse_tsr_gentime(tsr_data)

    except Exception:
        logger.exception("Failed to query TSA at %s", tsa_url)
        return None


def _build_timestamp_request(digest: bytes) -> bytes:
    """Build a minimal DER-encoded RFC 3161 TimeStampReq.

    This constructs a basic ASN.1 DER structure for the request.
    """
    # SHA-256 OID: 2.16.840.1.101.3.4.2.1
    sha256_oid = bytes([
        0x30, 0x0D, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01,
        0x65, 0x03, 0x04, 0x02, 0x01, 0x05, 0x00,
    ])

    # MessageImprint ::= SEQUENCE { hashAlgorithm, hashedMessage }
    hashed_message = bytes([0x04, len(digest)]) + digest
    msg_imprint_content = sha256_oid + hashed_message
    msg_imprint = (
        bytes([0x30, len(msg_imprint_content)]) + msg_imprint_content
    )

    # Version (INTEGER 1)
    version = bytes([0x02, 0x01, 0x01])

    # CertReq (BOOLEAN TRUE)
    cert_req = bytes([0x01, 0x01, 0xFF])

    # TimeStampReq ::= SEQUENCE { version, messageImprint, certReq }
    req_content = version + msg_imprint + cert_req
    tsq = bytes([0x30, len(req_content)]) + req_content

    return tsq


def _parse_tsr_gentime(tsr_data: bytes) -> Optional[datetime]:
    """Extract genTime from a DER-encoded TimeStampResp.

    Searches for GeneralizedTime (tag 0x18) in the response.
    Returns None if parsing fails.
    """
    try:
        # Look for GeneralizedTime tag (0x18) in the response
        idx = 0
        while idx < len(tsr_data) - 1:
            if tsr_data[idx] == 0x18:
                length = tsr_data[idx + 1]
                time_str = tsr_data[idx + 2: idx + 2 + length].decode("ascii")
                return _parse_generalized_time(time_str)
            idx += 1
        return None
    except Exception:
        logger.exception("Failed to parse TSR genTime")
        return None


def _parse_generalized_time(time_str: str) -> datetime:
    """Parse ASN.1 GeneralizedTime string to datetime."""
    # Format: YYYYMMDDHHMMSSZ or YYYYMMDDHHMMSS.fffZ
    time_str = time_str.rstrip("Z")
    if "." in time_str:
        dt = datetime.strptime(time_str, "%Y%m%d%H%M%S.%f")
    else:
        dt = datetime.strptime(time_str, "%Y%m%d%H%M%S")
    return dt.replace(tzinfo=timezone.utc)
