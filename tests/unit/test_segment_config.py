"""Tests for segment (chunk) size configuration.

Validates:
- Default chunk_size is 64 GB at parameter level
- Minimum chunk_size (1 GB) works correctly
- Sub-minimum chunk_size raises EncryptionError
- Super-maximum chunk_size raises EncryptionError
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from desktop.crypto import EncryptionError, encrypt_file
from desktop.crypto.aes_gcm_encrypt import (
    _DEFAULT_CHUNK_SIZE,
    _MAX_CHUNK_SIZE,
    _MIN_CHUNK_SIZE,
)
from tests.fixtures.generate_test_files import SIZE_1MB, create_random_file

_1GB = 1 * 1024**3
_64GB = 64 * 1024**3


# ---------------------------------------------------------------------------
# Default value verification
# ---------------------------------------------------------------------------

class TestDefaultChunkSize:
    """The default chunk_size parameter must be 64 GB."""

    def test_default_constant_is_64gb(self) -> None:
        assert _DEFAULT_CHUNK_SIZE == _64GB

    def test_function_signature_default(self) -> None:
        sig = inspect.signature(encrypt_file)
        default = sig.parameters["chunk_size"].default
        assert default == _64GB

    def test_min_constant_is_1gb(self) -> None:
        assert _MIN_CHUNK_SIZE == _1GB

    def test_max_constant_is_64gb(self) -> None:
        assert _MAX_CHUNK_SIZE == _64GB


# ---------------------------------------------------------------------------
# Minimum chunk_size (1 GB)
# ---------------------------------------------------------------------------

class TestMinChunkSize:
    """Encryption with chunk_size = 1 GB on a small file."""

    def test_encrypt_with_1gb_chunk(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "min_chunk.enc")
        result = encrypt_file(
            file_1mb, aes_key, enc_path, chunk_size=_1GB,
        )
        assert result.chunk_count == 1
        assert os.path.isfile(enc_path)


# ---------------------------------------------------------------------------
# Below minimum -> error
# ---------------------------------------------------------------------------

class TestBelowMinChunkSize:
    """chunk_size below 1 GB must be rejected."""

    def test_512mb_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "too_small.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=512 * 1024**2,  # 512 MB
            )

    def test_1mb_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "1mb_chunk.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=1 * 1024**2,  # 1 MB
            )

    def test_zero_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "zero_chunk.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=0,
            )

    def test_negative_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "neg_chunk.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=-1,
            )


# ---------------------------------------------------------------------------
# Above maximum -> error
# ---------------------------------------------------------------------------

class TestAboveMaxChunkSize:
    """chunk_size above 64 GB must be rejected."""

    def test_65gb_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "too_large.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=65 * 1024**3,
            )

    def test_128gb_raises_error(
        self, file_1mb: str, aes_key: bytes, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "128gb_chunk.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(
                file_1mb, aes_key, enc_path,
                chunk_size=128 * 1024**3,
            )


# ---------------------------------------------------------------------------
# Invalid key size
# ---------------------------------------------------------------------------

class TestInvalidKeySize:
    """AES key must be exactly 32 bytes."""

    def test_16byte_key_raises(
        self, file_1mb: str, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "bad_key.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(file_1mb, os.urandom(16), enc_path)

    def test_empty_key_raises(
        self, file_1mb: str, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "empty_key.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(file_1mb, b"", enc_path)

    def test_64byte_key_raises(
        self, file_1mb: str, tmp_work_dir: Path
    ) -> None:
        enc_path = str(tmp_work_dir / "long_key.enc")
        with pytest.raises(EncryptionError):
            encrypt_file(file_1mb, os.urandom(64), enc_path)
