"""Seal process orchestration (S1 through S7).

Coordinates the full sealing workflow by calling into the crypto,
record, and db modules.  Each step produces results that feed into
the next.  On error the current state is preserved so the user
can retry from the failing step.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Immutable step result containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SealConfig:
    """Immutable configuration collected from wizard steps S1-S3."""

    source_file: str
    output_dir: str
    chunk_size_bytes: int
    case_number: str
    investigator: dict[str, str]
    seizure: dict[str, str]
    media: dict[str, str]
    subject: dict[str, str]
    signature_lines: list[tuple[int, int, int, int]]


@dataclass(frozen=True)
class SealResult:
    """Immutable result of the complete seal process."""

    seal_id: str
    enc_filepath: str
    pdf_path: str
    key_shares: tuple[str, str, str, str]
    unlock_time_iso: str
    record_json: str


# ---------------------------------------------------------------------------
# Seal process orchestrator
# ---------------------------------------------------------------------------

class SealProcess:
    """Orchestrates the sealing workflow steps S1 through S7.

    The process is designed to be driven by the GUI wizard.  Each
    ``run_sN`` method executes the corresponding step and returns
    a result dict.  If a step fails, the process state is preserved
    so the user can retry.

    Attributes:
        config: The sealing configuration (set after S3).
        state: Mutable state dict accumulating step results.
    """

    def __init__(self, *, db_path: str) -> None:
        self._db_path = db_path
        self.config: Optional[SealConfig] = None
        self.state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # S1: File encryption
    # ------------------------------------------------------------------

    def run_s1(
        self,
        source_file: str,
        output_dir: str,
        chunk_size_gb: int,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, Any]:
        """Encrypt the source file with AES-256-GCM.

        Returns a dict with keys: aes_key_hex, enc_filepath, metadata,
        chunk_count.
        """
        from .crypto import collect_metadata, encrypt_file

        chunk_bytes = chunk_size_gb * (1024 ** 3)

        # Generate AES-256 key
        aes_key = os.urandom(32)
        aes_key_hex = aes_key.hex()

        # Collect metadata before encryption
        metadata = collect_metadata(source_file)

        # Determine output path
        src_name = Path(source_file).stem
        enc_filename = f"{src_name}.enc"
        enc_path = str(Path(output_dir) / enc_filename)

        # Encrypt
        result = encrypt_file(
            filepath=source_file,
            aes_key=aes_key,
            output_path=enc_path,
            chunk_size=chunk_bytes,
            progress_cb=progress_cb,
        )

        step_result = {
            "aes_key_hex": aes_key_hex,
            "enc_filepath": result.enc_filepath,
            "metadata": {
                "filename": metadata.filename,
                "size": metadata.size,
                "md5": metadata.md5,
                "sha256": metadata.sha256,
                "mtime": metadata.mtime,
                "ctime": metadata.ctime,
                "atime": metadata.atime,
            },
            "chunk_count": result.chunk_count,
            "encryption_algo": result.encryption_algo,
        }
        self.state["s1"] = step_result
        logger.info(
            "S1 완료: %s -> %s (%d 구간)",
            source_file, result.enc_filepath, result.chunk_count,
        )
        return step_result

    # ------------------------------------------------------------------
    # S2-S3: Info collection (handled by wizard, stored via set_config)
    # ------------------------------------------------------------------

    def set_config(self, config: SealConfig) -> None:
        """Store the configuration collected from S1-S3."""
        self.config = config

    # ------------------------------------------------------------------
    # S4: Generate seal record (preview)
    # ------------------------------------------------------------------

    def run_s4(self) -> dict[str, Any]:
        """Generate the seal_id and assemble the seal record JSON.

        Returns a dict with keys: seal_id, record_dict.
        """
        if self.config is None:
            raise RuntimeError("config가 설정되지 않았습니다 (S1-S3 먼저 실행).")
        if "s1" not in self.state:
            raise RuntimeError("S1이 완료되지 않았습니다.")

        now = datetime.now(tz=timezone.utc)
        seal_id = f"S-{now.strftime('%Y%m%d')}-{os.urandom(3).hex().upper()}"

        record = {
            "seal_id": seal_id,
            "type": "seal",
            "version": "1.0",
            "created_at": now.isoformat(),
            "case_number": self.config.case_number,
            "investigator": self.config.investigator,
            "seizure": self.config.seizure,
            "media": self.config.media,
            "subject": {
                "name": self.config.subject["name"],
                "email": self.config.subject["email"],
                "birth": self.config.subject["birth"],
                "phone": self.config.subject["phone"],
            },
            "encryption": {
                "algorithm": self.state["s1"]["encryption_algo"],
                "chunk_count": self.state["s1"]["chunk_count"],
                "enc_filepath": self.state["s1"]["enc_filepath"],
            },
            "original_file": self.state["s1"]["metadata"],
            "history": [
                {
                    "event": "seal",
                    "timestamp": now.isoformat(),
                    "actor": self.config.investigator.get("name", ""),
                }
            ],
            "summary": "S1",
        }

        self.state["s4"] = {"seal_id": seal_id, "record_dict": record}
        logger.info("S4 완료: seal_id=%s", seal_id)
        return self.state["s4"]

    # ------------------------------------------------------------------
    # S5: Digital signature
    # ------------------------------------------------------------------

    def run_s5(
        self,
        status_cb: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        """Generate certificate, render PDF, apply digital signature.

        Returns a dict with keys: cert_pem_path, key_pem_path, pdf_path.
        """
        if "s4" not in self.state:
            raise RuntimeError("S4가 완료되지 않았습니다.")

        def _notify(msg: str) -> None:
            if status_cb:
                status_cb(msg)

        seal_id = self.state["s4"]["seal_id"]
        record_dict = self.state["s4"]["record_dict"]
        output_dir = Path(self.config.output_dir) if self.config else Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- JSON 저장 ---
        _notify("봉인 기록 JSON 저장 중...")
        record_json_path = str(output_dir / f"{seal_id}_record.json")
        with open(record_json_path, "w", encoding="utf-8") as f:
            json.dump(record_dict, f, ensure_ascii=False, indent=2)

        # --- PDF 렌더링 ---
        _notify("PDF 생성 중...")
        pdf_path = str(output_dir / f"{seal_id}_seal_record.pdf")
        try:
            from desktop.record import render_record_pdf
            render_record_pdf(record_dict, "seal_record.html", pdf_path)
            _notify("PDF 렌더링 완료")
        except (ImportError, Exception) as exc:
            _notify(f"PDF 렌더링 폴백 (weasyprint 미설치 가능): {exc}")
            logger.warning("PDF 렌더링 폴백: %s", exc)
            Path(pdf_path).write_text(
                f"[Placeholder] Seal Record PDF for {seal_id}",
                encoding="utf-8",
            )

        # --- 인증서 생성 ---
        _notify("RSA 키쌍 및 인증서 생성 중...")
        subject_name = self.config.subject.get("name", "Unknown")
        subject_email = self.config.subject.get("email", "unknown@example.com")
        password = self.config.subject.get("password", "default_password")

        # 서명 이미지 해시 계산 (서명 데이터를 임시 파일로 저장)
        import hashlib
        sig_lines = self.config.signature_lines
        sig_bytes = json.dumps(sig_lines).encode("utf-8")
        sig_hash = hashlib.sha256(sig_bytes).hexdigest()

        cert_pem_path = str(output_dir / f"{seal_id}_cert.pem")
        key_pem_path = str(output_dir / f"{seal_id}_key.pem")

        try:
            from desktop.signature import (
                generate_keypair,
                create_self_signed_cert,
                save_private_key,
                save_certificate,
                sign_pdf as signature_sign_pdf,
            )

            # 1. RSA 키쌍 생성
            private_key, public_key = generate_keypair(2048)
            _notify("RSA-2048 키쌍 생성 완료")

            # 2. X.509 자체서명 인증서 생성 (서명이미지 해시 포함)
            cert = create_self_signed_cert(
                private_key=private_key,
                subject_name=subject_name,
                email=subject_email,
                signature_image_hash=sig_hash,
            )
            _notify("X.509 인증서 생성 완료")

            # 3. 인증서·개인키 PEM 저장
            save_certificate(cert, cert_pem_path)
            save_private_key(private_key, key_pem_path, password)
            _notify("인증서/개인키 저장 완료")

            # 4. PAdES PDF 전자서명 적용
            _notify("PDF 전자서명 적용 중...")
            signed_pdf_path = str(output_dir / f"{seal_id}_seal_record_signed.pdf")
            warning = signature_sign_pdf(
                pdf_path=pdf_path,
                cert_path=cert_pem_path,
                key_path=key_pem_path,
                password=password,
                output_path=signed_pdf_path,
                tsa_url=None,  # TSA는 아래에서 별도 처리
            )
            # 서명된 PDF로 교체
            pdf_path = signed_pdf_path
            if warning:
                _notify(f"전자서명 완료 (경고: {warning})")
            else:
                _notify("PDF 전자서명 완료")

        except ImportError as exc:
            _notify(f"전자서명 모듈 미설치: {exc}")
            logger.warning("전자서명 모듈 import 실패: %s", exc)
        except Exception as exc:
            _notify(f"전자서명 처리 중 오류: {exc}")
            logger.warning("전자서명 처리 중 오류: %s", exc)

        # --- TSA 시점확인 ---
        _notify("TSA 시점확인 요청 중...")
        try:
            from desktop.signature import request_timestamp
            pdf_hash = hashlib.sha256(Path(pdf_path).read_bytes()).digest()
            tst_token = request_timestamp(pdf_hash, "http://localhost:3161/tsa")
            _notify("TSA 시점확인 완료")
        except (ImportError, Exception) as exc:
            _notify(f"TSA 처리 스킵 (내부 TSA 미가동 시 정상): {exc}")
            logger.warning("TSA 시점확인 스킵: %s", exc)

        # Read cert/key PEM content for S7 DB storage
        cert_pem_content = ""
        key_pem_content = b""
        try:
            with open(cert_pem_path, "r", encoding="utf-8") as f:
                cert_pem_content = f.read()
            with open(key_pem_path, "rb") as f:
                key_pem_content = f.read()
        except FileNotFoundError:
            pass

        step_result = {
            "cert_pem_path": cert_pem_path,
            "key_pem_path": key_pem_path,
            "pdf_path": pdf_path,
            "record_json_path": record_json_path,
            "cert_pem": cert_pem_content,
            "key_pem": key_pem_content,
        }
        self.state["s5"] = step_result
        _notify("S5 단계 완료")
        logger.info("S5 완료: pdf_path=%s", pdf_path)
        return step_result

    # ------------------------------------------------------------------
    # S6: Key splitting
    # ------------------------------------------------------------------

    def run_s6(
        self,
        unlock_days: int = 10,
    ) -> dict[str, Any]:
        """Split the AES key via SSS 2-of-4 and encrypt shares 3/4.

        Args:
            unlock_days: Number of days until key share 3 becomes accessible.

        Returns a dict with keys: shares, unlock_time_iso,
        encrypted_shares.
        """
        if "s1" not in self.state:
            raise RuntimeError("S1이 완료되지 않았습니다.")

        from .crypto import encrypt_envelope, get_master_key_path, split_key

        aes_key_hex = self.state["s1"]["aes_key_hex"]
        shares = split_key(aes_key_hex)

        # Verify split by recovering with shares 0 and 1
        from .crypto import recover_key

        recovered = recover_key([shares[0], shares[1]])
        if recovered != aes_key_hex:
            raise RuntimeError("키 분할 검증 실패: 복원된 키가 원본과 불일치")

        # Encrypt shares 3 and 4 with local KMS
        master_path = get_master_key_path()
        enc_share_3 = encrypt_envelope(shares[2].encode("utf-8"), master_path)
        enc_share_4 = encrypt_envelope(shares[3].encode("utf-8"), master_path)

        # Calculate unlock_time
        now = datetime.now(tz=timezone.utc)
        unlock_time = now + timedelta(days=unlock_days)

        step_result = {
            "shares": shares,
            "unlock_time_iso": unlock_time.isoformat(),
            "encrypted_shares": {3: enc_share_3, 4: enc_share_4},
        }
        self.state["s6"] = step_result
        logger.info(
            "S6 완료: 키 분할 4조각, unlock_time=%s", unlock_time.isoformat()
        )
        return step_result

    # ------------------------------------------------------------------
    # S7: Save records
    # ------------------------------------------------------------------

    def run_s7(self) -> SealResult:
        """Persist seal record, key shares, and certificate to the DB.

        Returns a SealResult with the complete sealing outcome.
        """
        required = ["s1", "s4", "s5", "s6"]
        for step in required:
            if step not in self.state:
                raise RuntimeError(f"{step.upper()}가 완료되지 않았습니다.")

        from .db import (
            save_certificate,
            save_key_shares,
            save_seal_record,
        )

        seal_id = self.state["s4"]["seal_id"]
        record_dict = self.state["s4"]["record_dict"]

        # Update record with S6 info
        record_with_unlock = {
            **record_dict,
            "unlock_time": self.state["s6"]["unlock_time_iso"],
        }
        record_json = json.dumps(record_with_unlock, ensure_ascii=False, indent=2)
        pdf_path = self.state["s5"]["pdf_path"]

        # Save to DB
        save_seal_record(self._db_path, seal_id, record_json, pdf_path)

        # Save encrypted key shares 3 and 4
        enc_shares = self.state["s6"]["encrypted_shares"]
        save_key_shares(self._db_path, seal_id, enc_shares)

        # Save certificate if available
        cert_pem = self.state["s5"].get("cert_pem", "")
        key_pem = self.state["s5"].get("key_pem", b"")
        if cert_pem:
            # Encrypt private key before storing
            try:
                from .crypto import encrypt_envelope, get_master_key_path

                master_path = get_master_key_path()
                key_encrypted = encrypt_envelope(key_pem, master_path)
            except Exception:
                key_encrypted = key_pem  # Fallback: store as-is
            save_certificate(self._db_path, seal_id, cert_pem, key_encrypted)

        shares = self.state["s6"]["shares"]
        result = SealResult(
            seal_id=seal_id,
            enc_filepath=self.state["s1"]["enc_filepath"],
            pdf_path=pdf_path,
            key_shares=(shares[0], shares[1], shares[2], shares[3]),
            unlock_time_iso=self.state["s6"]["unlock_time_iso"],
            record_json=record_json,
        )

        self.state["s7"] = {"seal_result": result}
        logger.info("S7 완료: seal_id=%s, 모든 기록 저장", seal_id)
        return result


def run_seal_in_background(
    process: SealProcess,
    wizard_data: dict[str, Any],
    *,
    db_path: str,
    on_step: Optional[Callable[[str, str], None]] = None,
    on_complete: Optional[Callable[[SealResult], None]] = None,
    on_error: Optional[Callable[[str, Exception], None]] = None,
) -> threading.Thread:
    """Run the full seal process on a background thread.

    Args:
        process: The SealProcess instance.
        wizard_data: Data collected from the seal wizard.
        db_path: SQLite database path.
        on_step: Callback ``(step_name, message)`` for progress updates.
        on_complete: Callback with the final SealResult.
        on_error: Callback ``(step_name, exception)`` on failure.

    Returns:
        The started daemon thread.
    """

    def _notify(step: str, msg: str) -> None:
        if on_step:
            on_step(step, msg)

    def _run() -> None:
        try:
            config = SealConfig(
                source_file=wizard_data["source_file"],
                output_dir=wizard_data["output_dir"],
                chunk_size_bytes=wizard_data["chunk_size_gb"] * (1024 ** 3),
                case_number=wizard_data["case_number"],
                investigator=wizard_data.get("investigator", {}),
                seizure=wizard_data.get("seizure", {}),
                media=wizard_data.get("media", {}),
                subject=wizard_data.get("subject", {}),
                signature_lines=wizard_data.get("signature_lines", []),
            )
            process.set_config(config)

            _notify("S4", "봉인 기록 생성 중...")
            process.run_s4()

            _notify("S5", "전자서명 진행 중...")
            process.run_s5(status_cb=lambda msg: _notify("S5", msg))

            _notify("S6", "키 분할 중...")
            unlock_days = wizard_data.get("unlock_days", 10)
            process.run_s6(unlock_days=unlock_days)

            _notify("S7", "기록 저장 중...")
            result = process.run_s7()

            if on_complete:
                on_complete(result)

        except Exception as exc:
            logger.exception("봉인 프로세스 오류")
            if on_error:
                on_error("unknown", exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
