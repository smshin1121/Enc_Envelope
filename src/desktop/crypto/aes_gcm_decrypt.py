"""AES-256-GCM segmented file decryption with tag verification."""

from __future__ import annotations

import json
import logging
import os
import struct
from typing import Callable, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import DecryptionError, TamperDetectedError
from .file_metadata import _compute_hashes
from .types import DecryptionResult

logger = logging.getLogger(__name__)

_OFFSET_SIZE = 8
_META_SIZE_FIELD = 4
_TAG_SIZE = 16
_READ_BUFFER = 64 * 1024 * 1024  # 64 MB


def decrypt_file(
    enc_filepath: str,
    aes_key: bytes,
    output_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> DecryptionResult:
    """Decrypt an .enc file produced by encrypt_file.

    Reads metadata from file tail, decrypts each segment with its
    nonce and verifies its auth tag. After full decryption, compares
    MD5 and SHA-256 hashes against the originals stored in metadata.

    Args:
        enc_filepath: Path to the .enc file.
        aes_key: 32-byte AES-256 key.
        output_dir: Directory to write the decrypted file.
        progress_cb: Optional callback(completed_bytes, total_bytes).

    Returns:
        DecryptionResult with verification details.

    Raises:
        DecryptionError: On decryption failure.
        TamperDetectedError: If any auth tag verification fails.
    """
    _validate_inputs(enc_filepath, aes_key, output_dir)

    meta = _read_metadata(enc_filepath)
    output_path = os.path.join(output_dir, meta["filename"])

    nonces = [bytes.fromhex(n) for n in meta["nonces"]]
    tags = [bytes.fromhex(t) for t in meta["tags"]]
    chunk_lengths = meta["chunk_lengths"]
    num_chunks = len(nonces)
    total_encrypted_size = sum(cl + _TAG_SIZE for cl in chunk_lengths)

    try:
        aesgcm = AESGCM(aes_key)

        _decrypt_chunks(
            enc_filepath=enc_filepath,
            output_path=output_path,
            aesgcm=aesgcm,
            nonces=nonces,
            tags=tags,
            chunk_lengths=chunk_lengths,
            num_chunks=num_chunks,
            total_original_size=meta["size"],
            progress_cb=progress_cb,
        )
    except TamperDetectedError:
        _cleanup_partial(output_path)
        raise
    except Exception as exc:
        _cleanup_partial(output_path)
        raise DecryptionError(f"Decryption failed: {exc}") from exc

    # Verify hashes
    md5_match, sha256_match = _verify_hashes(
        output_path, meta.get("hash_before_md5"), meta.get("hash_before_sha256")
    )

    return DecryptionResult(
        output_filepath=output_path,
        original_filename=meta["filename"],
        hash_verified=md5_match and sha256_match,
        sha256_match=sha256_match,
        md5_match=md5_match,
        metadata=meta,
    )


def _validate_inputs(
    enc_filepath: str, aes_key: bytes, output_dir: str
) -> None:
    """Validate decryption inputs."""
    if not os.path.isfile(enc_filepath):
        raise DecryptionError(f"Encrypted file not found: {enc_filepath}")
    if len(aes_key) != 32:
        raise DecryptionError(f"AES key must be 32 bytes, got {len(aes_key)}")
    if not os.path.isdir(output_dir):
        raise DecryptionError(f"Output directory not found: {output_dir}")


def _read_metadata(enc_filepath: str) -> dict:
    """Read and parse metadata JSON from the .enc file tail.

    Reads the last 4 bytes for meta_size, then reads the JSON block.
    Also reads the first 8 bytes for offset verification.
    """
    try:
        file_size = os.path.getsize(enc_filepath)
        if file_size < _OFFSET_SIZE + _META_SIZE_FIELD:
            raise DecryptionError("File too small to be a valid .enc file")

        with open(enc_filepath, "rb") as f:
            # Read offset from header
            offset_bytes = f.read(_OFFSET_SIZE)
            meta_offset = struct.unpack("<Q", offset_bytes)[0]

            # Read meta_size from tail
            f.seek(file_size - _META_SIZE_FIELD)
            meta_size_bytes = f.read(_META_SIZE_FIELD)
            meta_size = struct.unpack("<I", meta_size_bytes)[0]

            # Validate offset
            expected_offset = file_size - _META_SIZE_FIELD - meta_size
            if meta_offset != expected_offset:
                raise DecryptionError(
                    f"Metadata offset mismatch: header={meta_offset}, "
                    f"calculated={expected_offset}"
                )

            # Read metadata JSON
            f.seek(meta_offset)
            meta_json = f.read(meta_size)

        return json.loads(meta_json.decode("utf-8"))

    except (json.JSONDecodeError, struct.error) as exc:
        raise DecryptionError(f"Failed to parse metadata: {exc}") from exc
    except DecryptionError:
        raise
    except OSError as exc:
        raise DecryptionError(f"Failed to read .enc file: {exc}") from exc


def _decrypt_chunks(
    *,
    enc_filepath: str,
    output_path: str,
    aesgcm: AESGCM,
    nonces: list[bytes],
    tags: list[bytes],
    chunk_lengths: list[int],
    num_chunks: int,
    total_original_size: int,
    progress_cb: Optional[Callable[[int, int], None]],
) -> None:
    """Decrypt all chunks and write to output file."""
    completed_bytes = 0

    with open(enc_filepath, "rb") as src:
        src.seek(_OFFSET_SIZE)  # skip the 8-byte offset header

        with open(output_path, "wb") as dst:
            for i in range(num_chunks):
                ct_len = chunk_lengths[i] + _TAG_SIZE
                ciphertext_with_tag = _read_exact(src, ct_len)

                # Verify the stored tag matches
                stored_tag = tags[i]
                actual_tag = ciphertext_with_tag[-_TAG_SIZE:]
                if actual_tag != stored_tag:
                    raise TamperDetectedError(
                        f"Auth tag mismatch at chunk {i}: "
                        f"data may have been tampered with"
                    )

                try:
                    plaintext = aesgcm.decrypt(
                        nonces[i], ciphertext_with_tag, None
                    )
                except Exception as exc:
                    raise TamperDetectedError(
                        f"GCM decryption failed at chunk {i}: {exc}"
                    ) from exc

                dst.write(plaintext)
                completed_bytes += len(plaintext)

                if progress_cb is not None:
                    progress_cb(completed_bytes, total_original_size)


def _verify_hashes(
    filepath: str,
    expected_md5: Optional[str],
    expected_sha256: Optional[str],
) -> tuple[bool, bool]:
    """Verify MD5 and SHA-256 hashes of the decrypted file."""
    actual_md5, actual_sha256 = _compute_hashes(filepath)

    md5_match = (expected_md5 is None) or (actual_md5 == expected_md5)
    sha256_match = (expected_sha256 is None) or (actual_sha256 == expected_sha256)

    if not md5_match:
        logger.error(
            "MD5 mismatch: expected=%s, actual=%s", expected_md5, actual_md5
        )
    if not sha256_match:
        logger.error(
            "SHA-256 mismatch: expected=%s, actual=%s",
            expected_sha256, actual_sha256,
        )

    return md5_match, sha256_match


def _read_exact(f, size: int) -> bytes:
    """Read exactly `size` bytes from file."""
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        to_read = min(_READ_BUFFER, remaining)
        data = f.read(to_read)
        if not data:
            break
        parts.append(data)
        remaining -= len(data)
    return b"".join(parts)


def _cleanup_partial(path: str) -> None:
    """Remove partially decrypted file."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.warning("Failed to clean up partial file: %s", path)
