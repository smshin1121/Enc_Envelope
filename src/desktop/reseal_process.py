"""Reseal process orchestration (R1 through R8).

Coordinates the full resealing workflow by calling into the crypto,
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
# Immutable result containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResealConfig:
    """Immutable configuration collected from wizard steps."""

    source_dir: str
    output_dir: str
    chunk_size_bytes: int
    investigator: str
    reason: str
    subject_participated: bool


@dataclass(frozen=True)
class UnknownFileInfo:
    """Classification result for a single unknown file."""

    filepath: str
    filename: str
    size: int
    sha256: str
    suggested_category: str
    classification: str  # "derived" | "excluded"
    parent_file: str  # original file for derived files
    derivation_reason: str  # reason for derivation


@dataclass(frozen=True)
class ResealResult:
    """Immutable result of the complete reseal process."""

    seal_id: str
    enc_filepath: str
    pdf_path: str
    key_shares: tuple[str, str, str, str]
    unlock_time_iso: str
    record_json: str


# ---------------------------------------------------------------------------
# Reseal process orchestrator
# ---------------------------------------------------------------------------

class ResealProcess:
    """Orchestrates the resealing workflow steps R1 through R8.

    Driven by the ResealWizard GUI.  Each ``run_rN`` method executes
    the corresponding step and returns a result dict.
    """

    def __init__(self, *, db_path: str) -> None:
        self._db_path = db_path
        self.config: Optional[ResealConfig] = None
        self.state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # R1: Load previous record
    # ------------------------------------------------------------------

    def run_r1_load(self, record_path: str) -> dict[str, Any]:
        """Load and validate the previous unseal record JSON.

        Returns dict with keys: prev_record, seal_id.
        """
        path = Path(record_path)
        if not path.exists():
            raise ValueError(f"기록지 파일이 존재하지 않습니다: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                prev_record = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 파싱 오류: {exc}") from exc

        seal_id = prev_record.get("seal_id", "")
        if not seal_id:
            raise ValueError("기록지에 seal_id가 없습니다.")

        step_result = {
            "prev_record": prev_record,
            "seal_id": seal_id,
            "record_path": record_path,
        }
        self.state["r1"] = step_result
        logger.info("R1 기록지 로드 완료: seal_id=%s", seal_id)
        return step_result

    # ------------------------------------------------------------------
    # R2: File comparison / Unknown identification
    # ------------------------------------------------------------------

    def run_r2_compare(self, target_dir: str) -> dict[str, Any]:
        """Compare files in target_dir against the previous record.

        Returns dict with keys: known_files, unknown_files.
        """
        if "r1" not in self.state:
            raise RuntimeError("R1이 완료되지 않았습니다.")

        prev_record = self.state["r1"]["prev_record"]

        known_files: list[dict[str, Any]] = []
        unknown_files: list[dict[str, Any]] = []

        try:
            from .record import identify_unknown_files

            known_files, unknown_files = identify_unknown_files(
                prev_record=prev_record,
                current_dir=target_dir,
            )
        except ImportError:
            logger.warning("unknown_classifier 미구현 - 직접 비교")
            known_files, unknown_files = _fallback_classify(
                prev_record, target_dir
            )

        step_result = {
            "known_files": known_files,
            "unknown_files": unknown_files,
            "target_dir": target_dir,
        }
        self.state["r2"] = step_result
        logger.info(
            "R2 파일 비교 완료: known=%d, unknown=%d",
            len(known_files),
            len(unknown_files),
        )
        return step_result

    # ------------------------------------------------------------------
    # R3: Unknown file classification results
    # ------------------------------------------------------------------

    def set_r3_classifications(
        self, classifications: list[UnknownFileInfo]
    ) -> dict[str, Any]:
        """Store the user's classification of unknown files.

        Args:
            classifications: List of classified unknown files.

        Returns dict with keys: derived_files, excluded_files.
        """
        derived = [c for c in classifications if c.classification == "derived"]
        excluded = [
            c for c in classifications if c.classification == "excluded"
        ]

        step_result = {
            "classifications": classifications,
            "derived_files": [
                {
                    "filepath": c.filepath,
                    "filename": c.filename,
                    "sha256": c.sha256,
                    "parent_file": c.parent_file,
                    "derivation_reason": c.derivation_reason,
                }
                for c in derived
            ],
            "excluded_files": [
                {
                    "filepath": c.filepath,
                    "filename": c.filename,
                    "sha256": c.sha256,
                }
                for c in excluded
            ],
        }
        self.state["r3"] = step_result
        logger.info(
            "R3 분류 완료: derived=%d, excluded=%d",
            len(derived),
            len(excluded),
        )
        return step_result

    # ------------------------------------------------------------------
    # R4: Reseal info collection
    # ------------------------------------------------------------------

    def set_config(self, config: ResealConfig) -> None:
        """Store configuration collected from R4."""
        self.config = config

    # ------------------------------------------------------------------
    # R5: Encryption
    # ------------------------------------------------------------------

    def run_r5_encrypt(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, Any]:
        """Encrypt files for resealing.

        Returns dict with keys: aes_key_hex, enc_filepath, metadata,
        chunk_count.
        """
        if self.config is None:
            raise RuntimeError("config가 설정되지 않았습니다 (R4).")
        if "r2" not in self.state:
            raise RuntimeError("R2가 완료되지 않았습니다.")

        from .crypto import collect_metadata, encrypt_file

        # Determine which files to encrypt
        # For simplicity, encrypt the source directory as a unit
        # In practice, files would be packed/archived first
        source_dir = self.state["r2"]["target_dir"]
        output_dir = self.config.output_dir

        # Generate new AES key
        aes_key = os.urandom(32)
        aes_key_hex = aes_key.hex()

        # Find the primary file to encrypt
        # Use the first known file or the directory itself
        known_files = self.state["r2"].get("known_files", [])
        derived_files = self.state.get("r3", {}).get("derived_files", [])

        # Collect all files to include
        files_to_encrypt: list[str] = []
        for kf in known_files:
            fp = kf.get("filepath", "")
            if fp and Path(fp).exists():
                files_to_encrypt.append(fp)
        for df in derived_files:
            fp = df.get("filepath", "")
            if fp and Path(fp).exists():
                files_to_encrypt.append(fp)

        enc_results: list[dict[str, Any]] = []
        total_files = len(files_to_encrypt)

        for idx, filepath in enumerate(files_to_encrypt):
            metadata = collect_metadata(filepath)
            src_name = Path(filepath).stem
            enc_filename = f"{src_name}.enc"
            enc_path = str(Path(output_dir) / enc_filename)

            def _wrapped_cb(current: int, total: int) -> None:
                if progress_cb:
                    # Scale progress across all files
                    overall = idx * 100 + int((current / max(total, 1)) * 100)
                    overall_total = total_files * 100
                    progress_cb(overall, overall_total)

            result = encrypt_file(
                filepath=filepath,
                aes_key=aes_key,
                output_path=enc_path,
                chunk_size=self.config.chunk_size_bytes,
                progress_cb=_wrapped_cb,
            )

            enc_results.append(
                {
                    "enc_filepath": result.enc_filepath,
                    "original_filepath": filepath,
                    "metadata": {
                        "filename": metadata.filename,
                        "size": metadata.size,
                        "md5": metadata.md5,
                        "sha256": metadata.sha256,
                    },
                    "chunk_count": result.chunk_count,
                }
            )

        step_result = {
            "aes_key_hex": aes_key_hex,
            "enc_results": enc_results,
            "encryption_algo": "AES-256-GCM",
        }
        self.state["r5"] = step_result
        logger.info("R5 암호화 완료: %d 파일", len(enc_results))
        return step_result

    # ------------------------------------------------------------------
    # R6: Reseal record generation
    # ------------------------------------------------------------------

    def run_r6_record(self) -> dict[str, Any]:
        """Generate the reseal record JSON and PDF.

        Returns dict with keys: record_dict, record_json_path, pdf_path.
        """
        if "r5" not in self.state or self.config is None:
            raise RuntimeError("R5가 완료되지 않았습니다.")

        prev_record = self.state["r1"]["prev_record"]
        seal_id = self.state["r1"]["seal_id"]
        now = datetime.now(tz=timezone.utc)
        output_dir = Path(self.config.output_dir)

        record_dict: dict[str, Any] = {}

        try:
            from .record import build_reseal_record

            process_info = {
                "type": "Resealing",
                "reason": self.config.reason,
                "investigator": self.config.investigator,
                "subject_participated": self.config.subject_participated,
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
            }
            file_info = {
                "enc_results": self.state["r5"]["enc_results"],
                "encryption_algo": self.state["r5"]["encryption_algo"],
            }
            unknown_files = self.state.get("r3", {}).get(
                "classifications", []
            )
            derived_files = self.state.get("r3", {}).get("derived_files", [])

            record_dict = build_reseal_record(
                prev_record=prev_record,
                process_info=process_info,
                file_info=file_info,
                unknown_files=[
                    {
                        "filename": u.filename,
                        "sha256": u.sha256,
                        "classification": u.classification,
                    }
                    for u in unknown_files
                    if isinstance(u, UnknownFileInfo)
                ],
                derived_files=derived_files,
            )
        except ImportError:
            logger.warning("record 모듈 미구현 - 직접 기록 구성")
            history = prev_record.get("history", [])
            new_event = {
                "event": "reseal",
                "timestamp": now.isoformat(),
                "actor": self.config.investigator,
                "reason": self.config.reason,
            }
            new_history = list(history) + [new_event]

            record_dict = {
                "seal_id": seal_id,
                "type": "reseal",
                "version": "1.0",
                "created_at": now.isoformat(),
                "case_number": prev_record.get("case_number", ""),
                "investigator": prev_record.get("investigator", {}),
                "reseal_info": {
                    "reason": self.config.reason,
                    "investigator": self.config.investigator,
                    "subject_participated": self.config.subject_participated,
                },
                "encryption": {
                    "algorithm": self.state["r5"]["encryption_algo"],
                    "enc_results": self.state["r5"]["enc_results"],
                },
                "known_files": self.state["r2"].get("known_files", []),
                "derived_files": self.state.get("r3", {}).get(
                    "derived_files", []
                ),
                "excluded_files": self.state.get("r3", {}).get(
                    "excluded_files", []
                ),
                "history": new_history,
                "summary": _compute_summary(new_history),
            }

        # Save JSON
        record_json_path = str(output_dir / f"{seal_id}_reseal_record.json")
        with open(record_json_path, "w", encoding="utf-8") as f:
            json.dump(record_dict, f, ensure_ascii=False, indent=2)

        # Render PDF
        pdf_path = str(output_dir / f"{seal_id}_reseal_record.pdf")
        try:
            from .record import render_record_pdf

            render_record_pdf(
                record=record_dict,
                template_name="reseal_record.html",
                output_path=pdf_path,
            )
        except ImportError:
            logger.warning("PDF 렌더링 모듈 미구현 - 경로만 기록")
            Path(pdf_path).write_text(
                f"[Placeholder] Reseal Record PDF for {seal_id}",
                encoding="utf-8",
            )

        step_result = {
            "record_dict": record_dict,
            "record_json_path": record_json_path,
            "pdf_path": pdf_path,
        }
        self.state["r6"] = step_result
        logger.info("R6 기록 생성 완료: %s", record_json_path)
        return step_result

    # ------------------------------------------------------------------
    # R7: Key splitting
    # ------------------------------------------------------------------

    def run_r7_split_key(
        self, unlock_days: int = 10
    ) -> dict[str, Any]:
        """Split the new AES key via SSS 2-of-4 and encrypt shares.

        Args:
            unlock_days: Days until key share 3 becomes accessible.

        Returns dict with keys: shares, unlock_time_iso,
        encrypted_shares.
        """
        if "r5" not in self.state:
            raise RuntimeError("R5가 완료되지 않았습니다.")

        from .crypto import encrypt_envelope, get_master_key_path, split_key
        from .crypto import recover_key

        aes_key_hex = self.state["r5"]["aes_key_hex"]
        shares = split_key(aes_key_hex)

        # Verify split
        recovered = recover_key([shares[0], shares[1]])
        if recovered != aes_key_hex:
            raise RuntimeError("키 분할 검증 실패: 복원된 키가 원본과 불일치")

        # Encrypt shares 3 and 4
        master_path = get_master_key_path()
        enc_share_3 = encrypt_envelope(shares[2].encode("utf-8"), master_path)
        enc_share_4 = encrypt_envelope(shares[3].encode("utf-8"), master_path)

        now = datetime.now(tz=timezone.utc)
        unlock_time = now + timedelta(days=unlock_days)

        step_result = {
            "shares": shares,
            "unlock_time_iso": unlock_time.isoformat(),
            "encrypted_shares": {3: enc_share_3, 4: enc_share_4},
        }
        self.state["r7"] = step_result
        logger.info(
            "R7 키 분할 완료: unlock_time=%s", unlock_time.isoformat()
        )
        return step_result

    # ------------------------------------------------------------------
    # R8: Save records
    # ------------------------------------------------------------------

    def run_r8_save(self) -> ResealResult:
        """Persist reseal record, key shares to the DB.

        Returns a ResealResult with the complete resealing outcome.
        """
        required = ["r1", "r5", "r6", "r7"]
        for step in required:
            if step not in self.state:
                raise RuntimeError(f"{step.upper()}가 완료되지 않았습니다.")

        from .db import save_key_shares, save_seal_record

        seal_id = self.state["r1"]["seal_id"]
        record_dict = self.state["r6"]["record_dict"]
        record_with_unlock = {
            **record_dict,
            "unlock_time": self.state["r7"]["unlock_time_iso"],
        }
        record_json = json.dumps(
            record_with_unlock, ensure_ascii=False, indent=2
        )
        pdf_path = self.state["r6"]["pdf_path"]

        save_seal_record(self._db_path, seal_id, record_json, pdf_path)

        enc_shares = self.state["r7"]["encrypted_shares"]
        save_key_shares(self._db_path, seal_id, enc_shares)

        shares = self.state["r7"]["shares"]
        enc_results = self.state["r5"].get("enc_results", [])
        enc_filepath = (
            enc_results[0]["enc_filepath"] if enc_results else ""
        )

        result = ResealResult(
            seal_id=seal_id,
            enc_filepath=enc_filepath,
            pdf_path=pdf_path,
            key_shares=(shares[0], shares[1], shares[2], shares[3]),
            unlock_time_iso=self.state["r7"]["unlock_time_iso"],
            record_json=record_json,
        )
        self.state["r8"] = {"reseal_result": result}
        logger.info("R8 완료: seal_id=%s, 모든 기록 저장", seal_id)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_summary(history: list[dict[str, Any]]) -> str:
    """Compute S{n}U{n}R{n} summary from history events."""
    seal_count = sum(1 for e in history if e.get("event") == "seal")
    unseal_count = sum(1 for e in history if e.get("event") == "unseal")
    reseal_count = sum(1 for e in history if e.get("event") == "reseal")
    return f"S{seal_count}U{unseal_count}R{reseal_count}"


def _fallback_classify(
    prev_record: dict[str, Any],
    target_dir: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Simple fallback file classification when record module is unavailable."""
    import hashlib

    # Build known hash set from previous record
    known_hashes: set[str] = set()
    original_file = prev_record.get("original_file", {})
    if original_file.get("sha256"):
        known_hashes.add(original_file["sha256"])

    # Also check file_info if present
    file_info = prev_record.get("file_info", {})
    for f in file_info.get("original_files", []):
        if f.get("sha256"):
            known_hashes.add(f["sha256"])

    known_files: list[dict[str, Any]] = []
    unknown_files: list[dict[str, Any]] = []

    target = Path(target_dir)
    if not target.exists():
        return known_files, unknown_files

    for filepath in target.rglob("*"):
        if not filepath.is_file():
            continue

        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        file_hash = h.hexdigest()

        file_entry = {
            "filepath": str(filepath),
            "filename": filepath.name,
            "size": filepath.stat().st_size,
            "sha256": file_hash,
        }

        if file_hash in known_hashes:
            known_files.append(file_entry)
        else:
            # Add suggested category
            ext = filepath.suffix.lower()
            category = "uncategorized"
            if ext in (".log", ".txt"):
                category = "analysis_log"
            elif ext in (".pdf", ".docx", ".xlsx"):
                category = "report"
            elif filepath.stat().st_size < 1024:
                category = "small_artifact"
            elif filepath.stat().st_size > 100 * 1024 * 1024:
                category = "large_artifact"

            file_entry["suggested_category"] = category
            unknown_files.append(file_entry)

    return known_files, unknown_files


def run_reseal_in_background(
    process: ResealProcess,
    wizard_data: dict[str, Any],
    *,
    db_path: str,
    on_step: Optional[Callable[[str, str], None]] = None,
    on_complete: Optional[Callable[[ResealResult], None]] = None,
    on_error: Optional[Callable[[str, Exception], None]] = None,
) -> threading.Thread:
    """Run the full reseal process on a background thread.

    Args:
        process: The ResealProcess instance.
        wizard_data: Data collected from the reseal wizard.
        db_path: SQLite database path.
        on_step: Callback ``(step_name, message)`` for progress updates.
        on_complete: Callback with the final ResealResult.
        on_error: Callback ``(step_name, exception)`` on failure.

    Returns:
        The started daemon thread.
    """

    def _notify(step: str, msg: str) -> None:
        if on_step:
            on_step(step, msg)

    def _run() -> None:
        try:
            _notify("R5", "암호화 진행 중...")
            process.run_r5_encrypt()

            _notify("R6", "재봉인기록지 생성 중...")
            process.run_r6_record()

            _notify("R7", "키 분할 중...")
            unlock_days = wizard_data.get("unlock_days", 10)
            process.run_r7_split_key(unlock_days=unlock_days)

            _notify("R8", "기록 저장 중...")
            result = process.run_r8_save()

            if on_complete:
                on_complete(result)

        except Exception as exc:
            logger.exception("재봉인 프로세스 오류")
            if on_error:
                on_error("unknown", exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
