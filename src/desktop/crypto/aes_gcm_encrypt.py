"""AES-256-GCM segmented file encryption with resume support."""

from __future__ import annotations

import json
import logging
import math
import os
import struct
from datetime import datetime, timezone
from typing import Callable, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .exceptions import EncryptionError
from .file_metadata import collect_metadata
from .types import EncryptionResult, FileMetadata

logger = logging.getLogger(__name__)

_MIN_CHUNK_SIZE = 1 * 1024**3          # 1 GB
_MAX_CHUNK_SIZE = 64 * 1024**3         # 64 GB
_DEFAULT_CHUNK_SIZE = 64 * 1024**3     # 64 GB
_OFFSET_SIZE = 8                        # bytes (uint64 LE)
_META_SIZE_FIELD = 4                    # bytes (uint32 LE)
_NONCE_SIZE = 12
_TAG_SIZE = 16
_READ_BUFFER = 64 * 1024 * 1024       # 64 MB read buffer


def encrypt_file(
    filepath: str,
    aes_key: bytes,
    output_path: str,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    seal_id: str = "",
) -> EncryptionResult:
    """Encrypt a file using AES-256-GCM with segmented processing.

    Each segment gets an independent nonce and produces an auth tag.
    The output .enc file structure:
        [8B offset LE] [encrypted data] [JSON metadata] [4B meta_size LE]

    Supports resume via .enc.progress file.

    Args:
        filepath: Path to the source file.
        aes_key: 32-byte AES-256 key.
        output_path: Path for the output .enc file.
        chunk_size: Segment size in bytes (1GB..64GB, default 64GB).
        progress_cb: Optional callback(completed_bytes, total_bytes).
        seal_id: Optional seal identifier for metadata.

    Returns:
        EncryptionResult with encryption details.

    Raises:
        EncryptionError: On any encryption failure.
    """
    _validate_inputs(filepath, aes_key, chunk_size)

    file_size = os.path.getsize(filepath)
    num_chunks = max(1, math.ceil(file_size / chunk_size))
    progress_path = output_path + ".progress"

    try:
        metadata = collect_metadata(filepath)
    except Exception as exc:
        raise EncryptionError(f"Metadata collection failed: {exc}") from exc

    # Load resume state if available
    resume = _load_progress(progress_path, filepath, chunk_size)
    start_chunk = resume["completed_chunks"] if resume else 0
    nonces: list[str] = resume["nonces"][:] if resume else []
    tags: list[str] = resume["tags"][:] if resume else []
    chunk_lengths: list[int] = resume["chunk_lengths"][:] if resume else []

    try:
        aesgcm = AESGCM(aes_key)

        _write_encrypted_data(
            filepath=filepath,
            output_path=output_path,
            aesgcm=aesgcm,
            file_size=file_size,
            chunk_size=chunk_size,
            num_chunks=num_chunks,
            start_chunk=start_chunk,
            nonces=nonces,
            tags=tags,
            chunk_lengths=chunk_lengths,
            progress_path=progress_path,
            progress_cb=progress_cb,
        )

        _write_metadata_and_finalize(
            output_path=output_path,
            metadata=metadata,
            nonces=nonces,
            tags=tags,
            chunk_lengths=chunk_lengths,
            seal_id=seal_id,
        )

        # Clean up progress file on success
        if os.path.exists(progress_path):
            os.remove(progress_path)

    except EncryptionError:
        raise
    except Exception as exc:
        raise EncryptionError(f"Encryption failed: {exc}") from exc

    return EncryptionResult(
        enc_filepath=output_path,
        original_filepath=filepath,
        metadata=metadata,
        chunk_count=num_chunks,
    )


def _validate_inputs(filepath: str, aes_key: bytes, chunk_size: int) -> None:
    """Validate encryption inputs."""
    if not os.path.isfile(filepath):
        raise EncryptionError(f"Source file not found: {filepath}")
    if len(aes_key) != 32:
        raise EncryptionError(f"AES key must be 32 bytes, got {len(aes_key)}")
    if not (_MIN_CHUNK_SIZE <= chunk_size <= _MAX_CHUNK_SIZE):
        raise EncryptionError(
            f"chunk_size must be between {_MIN_CHUNK_SIZE} and {_MAX_CHUNK_SIZE}"
        )


def _write_encrypted_data(
    *,
    filepath: str,
    output_path: str,
    aesgcm: AESGCM,
    file_size: int,
    chunk_size: int,
    num_chunks: int,
    start_chunk: int,
    nonces: list[str],
    tags: list[str],
    chunk_lengths: list[int],
    progress_path: str,
    progress_cb: Optional[Callable[[int, int], None]],
) -> None:
    """Encrypt file data chunk by chunk, writing to the output file."""
    # Calculate write position for resume
    data_offset = _OFFSET_SIZE  # skip the 8-byte offset header
    for cl in chunk_lengths:
        data_offset += cl + _TAG_SIZE  # each chunk produces ciphertext + tag

    mode = "r+b" if (start_chunk > 0 and os.path.exists(output_path)) else "wb"

    with open(filepath, "rb") as src:
        with open(output_path, mode) as dst:
            if mode == "wb":
                # Write placeholder offset (will be updated later)
                dst.write(struct.pack("<Q", 0))
            else:
                dst.seek(data_offset)

            # Seek source to the right position
            src.seek(start_chunk * chunk_size)
            completed_bytes = start_chunk * chunk_size

            for chunk_idx in range(start_chunk, num_chunks):
                remaining = file_size - (chunk_idx * chunk_size)
                current_chunk_size = min(chunk_size, remaining)

                plaintext = _read_exact(src, current_chunk_size)
                nonce = os.urandom(_NONCE_SIZE)

                ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)
                # ct_with_tag = ciphertext + tag(16 bytes)
                tag = ct_with_tag[-_TAG_SIZE:]
                ciphertext = ct_with_tag[:-_TAG_SIZE]

                dst.write(ct_with_tag)

                nonces.append(nonce.hex())
                tags.append(tag.hex())
                chunk_lengths.append(len(ciphertext))

                completed_bytes += current_chunk_size

                # Save progress after each chunk
                _save_progress(
                    progress_path, filepath, chunk_size,
                    len(nonces), nonces, tags, chunk_lengths,
                )

                if progress_cb is not None:
                    progress_cb(completed_bytes, file_size)


def _write_metadata_and_finalize(
    *,
    output_path: str,
    metadata: FileMetadata,
    nonces: list[str],
    tags: list[str],
    chunk_lengths: list[int],
    seal_id: str,
) -> None:
    """Write JSON metadata and update the offset header."""
    meta_dict = {
        "filename": metadata.filename,
        "size": metadata.size,
        "encryption_algo": "AES-256-GCM",
        "mtime": metadata.mtime,
        "ctime": metadata.ctime,
        "atime": metadata.atime,
        "enc_ended_time": datetime.now(tz=timezone.utc).isoformat(),
        "seal_id": seal_id,
        "nonces": nonces,
        "tags": tags,
        "chunk_lengths": chunk_lengths,
        "hash_before_sha256": metadata.sha256,
        "hash_before_md5": metadata.md5,
    }

    meta_json = json.dumps(meta_dict, ensure_ascii=False).encode("utf-8")
    meta_size = len(meta_json)

    with open(output_path, "r+b") as f:
        # Find current end of encrypted data
        f.seek(0, 2)
        meta_offset = f.tell()

        # Write metadata JSON
        f.write(meta_json)

        # Write meta_size (4 bytes LE)
        f.write(struct.pack("<I", meta_size))

        # Update offset at file start
        f.seek(0)
        f.write(struct.pack("<Q", meta_offset))


def _read_exact(f, size: int) -> bytes:
    """Read exactly `size` bytes from file, using buffered reads."""
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


def _save_progress(
    path: str,
    target_file: str,
    chunk_size: int,
    completed_chunks: int,
    nonces: list[str],
    tags: list[str],
    chunk_lengths: list[int],
) -> None:
    """Save encryption progress to a JSON file."""
    progress = {
        "target_file": os.path.basename(target_file),
        "completed_chunks": completed_chunks,
        "chunk_size": chunk_size,
        "nonces": nonces,
        "tags": tags,
        "chunk_lengths": chunk_lengths,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f)
    os.replace(tmp_path, path)


def _load_progress(
    path: str,
    target_file: str,
    chunk_size: int,
) -> Optional[dict]:
    """Load resume progress if valid. Returns None if no valid progress."""
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            progress = json.load(f)

        if (
            progress.get("target_file") != os.path.basename(target_file)
            or progress.get("chunk_size") != chunk_size
        ):
            logger.warning("Progress file mismatch, starting fresh")
            return None

        required = ["completed_chunks", "nonces", "tags", "chunk_lengths"]
        if not all(k in progress for k in required):
            return None

        return progress
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt progress file, starting fresh")
        return None
