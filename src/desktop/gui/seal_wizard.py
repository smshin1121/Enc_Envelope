"""Seal process wizard (S1 through S7).

Guides the investigator through the complete sealing workflow:
S1 - File selection and encryption settings
S2 - Seizure / sealing information
S3 - Subject (suspect) information
S4 - Seal record preview and review
S5 - Digital signature progress
S6 - Key splitting results and unlock_time setting
S7 - Completion summary
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import messagebox
from typing import TYPE_CHECKING, Any, Callable, Optional

from .i18n import t
from .step_indicator import StepIndicator
from .theme import COLORS, FONTS, get_color, get_font
from .signature_pad import EnhancedSignaturePad
from .widgets import DateEntry, FileSelector, LabeledEntry, SignaturePad

if TYPE_CHECKING:
    from .app import MainApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_CHUNK_GB = 1
MAX_CHUNK_GB = 64
DEFAULT_CHUNK_GB = 64
MIN_UNLOCK_DAYS = 1
MAX_UNLOCK_DAYS = 30
DEFAULT_UNLOCK_DAYS = 10


class SealWizard(tk.Frame):
    """Multi-step wizard for the sealing process.

    Each step is built as a separate frame.  Navigation buttons
    control the visible frame.
    """

    TOTAL_STEPS = 7

    def __init__(
        self,
        master: tk.Widget,
        app: MainApp,
        *,
        on_complete: Optional[Callable[[dict[str, Any]], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        prefill_data: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(master)
        self._app = app
        self._on_complete = on_complete
        self._on_cancel = on_cancel
        self._prefill_data = prefill_data
        self._current_step = 0
        self._data: dict[str, Any] = {}

        # Pre-set seal_id if provided by case workflow
        if prefill_data and prefill_data.get("seal_id"):
            self._data["seal_id"] = prefill_data["seal_id"]

        self._steps: list[tk.Frame] = []
        self._validators: list[Callable[[], bool]] = []

        self._build_layout()
        self._build_steps()
        self._apply_prefill()
        self._show_step(0)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        """Create the outer layout: header, step indicator, content area, nav bar."""
        from .theme import COLORS

        # Header — dark blue background for seal process
        _seal_header_bg = "#2c3e50"
        self._header = tk.Frame(self, bg=_seal_header_bg, height=56)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        self._title_label = tk.Label(
            self._header,
            text=t("seal.title"),
            fg="#ffffff",
            bg=_seal_header_bg,
            font=get_font("title"),
        )
        self._title_label.pack(side="left", padx=16)
        self._step_label = tk.Label(
            self._header,
            text="",
            fg="#a8c4e0",
            bg=_seal_header_bg,
            font=get_font("body"),
        )
        self._step_label.pack(side="right", padx=16)

        # Step indicator with subtle background
        step_bg_frame = tk.Frame(self, bg=get_color("step_bg"))
        step_bg_frame.pack(fill="x")
        self._step_indicator = StepIndicator(step_bg_frame, steps=[
            t("seal.step1"), t("seal.step2"), t("seal.step3"),
            t("seal.step4"), t("seal.step5"), t("seal.step6"), t("seal.step7"),
        ], on_step_click=self._on_step_click, bg=get_color("step_bg"))
        self._step_indicator.pack(fill="x", padx=16, pady=(8, 4))

        # Navigation bar — pack BEFORE content so it always gets space allocated.
        # In tkinter's packer, widgets packed later with expand=True can starve
        # earlier side="bottom" widgets when content is tall (e.g. S3).
        nav = tk.Frame(self, bg=get_color("card_bg"))
        nav.pack(fill="x", side="bottom")
        nav_border = tk.Frame(nav, height=1, bg=get_color("border"))
        nav_border.pack(fill="x", side="top")
        nav_inner = tk.Frame(nav, bg=get_color("card_bg"), padx=16, pady=8)
        nav_inner.pack(fill="x")

        # Cancel button — danger ghost
        self._cancel_btn = tk.Button(
            nav_inner, text=t("common.cancel"), width=12,
            command=self._handle_cancel,
            fg=get_color("danger"),
            bg=get_color("card_bg"),
            activebackground=get_color("error_bg"),
            activeforeground=get_color("danger"),
            relief="solid",
            bd=1,
            font=("맑은 고딕", 11, "bold"),
            padx=12, pady=6,
        )
        self._cancel_btn.pack(side="left")

        # Next button — prominent purple
        self._next_btn = tk.Button(
            nav_inner, text=t("common.next"), width=12,
            command=self._go_next,
            fg="#ffffff",
            bg=get_color("primary"),
            activebackground=get_color("primary_hover"),
            activeforeground="white",
            relief="flat",
            font=("맑은 고딕", 13, "bold"),
            padx=16, pady=6,
        )
        self._next_btn.pack(side="right")

        # Prev button — ghost style
        self._prev_btn = tk.Button(
            nav_inner, text=t("common.prev"), width=12,
            command=self._go_prev,
            fg=get_color("primary"),
            bg=get_color("card_bg"),
            activebackground=get_color("hover"),
            activeforeground=get_color("primary"),
            relief="solid",
            bd=1,
            font=("맑은 고딕", 11, "bold"),
            padx=12, pady=6,
        )
        self._prev_btn.pack(side="right", padx=(0, 8))

        # Content area — page background (packed AFTER nav so nav is guaranteed space)
        self._content = tk.Frame(self, bg=get_color("bg"), padx=24, pady=16)
        self._content.pack(fill="both", expand=True)

        # Keyboard shortcuts — skip if focus is inside an Entry widget
        self.winfo_toplevel().bind("<Return>", self._on_return_key)
        self.winfo_toplevel().bind("<Escape>", lambda _e: self._handle_cancel())

    # ------------------------------------------------------------------
    # Step builders
    # ------------------------------------------------------------------

    def _build_steps(self) -> None:
        """Build all 7 step frames."""
        builders = [
            self._build_s1,
            self._build_s2,
            self._build_s3,
            self._build_s4,
            self._build_s5,
            self._build_s6,
            self._build_s7,
        ]
        for builder in builders:
            frame = tk.Frame(self._content)
            builder(frame)
            self._steps.append(frame)

    def _apply_prefill(self) -> None:
        """Apply prefill data from case workflow to S2 and S3 fields."""
        pf = self._prefill_data
        if not pf:
            return

        # S2 fields
        case_number = pf.get("case_number", "")
        if case_number and hasattr(self, "_case_number"):
            self._case_number.set(case_number)

        investigator = pf.get("investigator", "")
        if investigator and hasattr(self, "_investigator_name"):
            self._investigator_name.set(investigator)

        # S3 fields
        suspect_name = pf.get("suspect_name", "")
        if suspect_name and hasattr(self, "_subject_name"):
            self._subject_name.set(suspect_name)

    # --- S1: File selection + encryption settings -------------------------

    def _build_s1(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("seal.s1_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._file_selector = FileSelector(
            parent,
            t("seal.target_file"),
            required=True,
            filetypes=[
                (t("filedialog.disk_images"), "*.dd *.e01 *.vmdk *.img *.raw"),
                (t("filedialog.all_files"), "*.*"),
            ],
        )
        self._file_selector.pack(fill="x", pady=4)

        self._output_selector = FileSelector(
            parent,
            t("seal.output_dir"),
            select_dir=True,
            required=True,
        )
        self._output_selector.pack(fill="x", pady=4)

        chunk_frame = tk.Frame(parent)
        chunk_frame.pack(fill="x", pady=8)
        tk.Label(
            chunk_frame,
            text=f"* {t('seal.chunk_size')}",
            anchor="w",
            width=20,
        ).pack(side="left")
        self._chunk_var = tk.IntVar(value=DEFAULT_CHUNK_GB)
        self._chunk_spin = tk.Spinbox(
            chunk_frame,
            from_=MIN_CHUNK_GB,
            to=MAX_CHUNK_GB,
            textvariable=self._chunk_var,
            width=6,
        )
        self._chunk_spin.pack(side="left")
        tk.Label(chunk_frame, text=t("seal.chunk_range")).pack(side="left", padx=8)

        self._validators.append(self._validate_s1)

    def _validate_s1(self) -> bool:
        errors: list[str] = []
        if not self._file_selector.is_valid():
            errors.append(t("validate.select_file"))
        if not self._output_selector.is_valid():
            errors.append(t("validate.select_output"))
        try:
            chunk = self._chunk_var.get()
            if not (MIN_CHUNK_GB <= chunk <= MAX_CHUNK_GB):
                errors.append(t("validate.chunk_range").format(min=MIN_CHUNK_GB, max=MAX_CHUNK_GB))
        except (tk.TclError, ValueError):
            errors.append(t("validate.chunk_invalid"))
        if errors:
            messagebox.showwarning(
                t("common.input_error"),
                "\n".join(errors),
                parent=self.winfo_toplevel(),
            )
            return False
        self._data["source_file"] = self._file_selector.get()
        self._data["output_dir"] = self._output_selector.get()
        self._data["chunk_size_gb"] = self._chunk_var.get()

        # 이미 암호화 완료된 경우 스킵
        if self._data.get("encryption_done"):
            return True

        # S1 검증 통과 → 바로 암호화 수행 (ProgressDialog)
        self._run_encryption()
        return self._data.get("encryption_done", False)

    def _run_encryption(self) -> None:
        """S1 파일 암호화를 ProgressDialog로 수행."""
        import os
        from pathlib import Path
        from .progress_dialog import ProgressDialog

        source = self._data["source_file"]
        output_dir = self._data["output_dir"]
        chunk_gb = self._data["chunk_size_gb"]
        chunk_bytes = chunk_gb * 1024 ** 3

        # AES 키 생성
        aes_key = os.urandom(32)
        self._data["aes_key"] = aes_key
        self._data["aes_key_hex"] = aes_key.hex()

        enc_filename = Path(source).name + ".enc"
        enc_path = str(Path(output_dir) / enc_filename)

        enc_result_holder: list = []
        enc_error_holder: list = []

        def task_fn(progress_cb):
            try:
                from desktop.crypto import encrypt_file, collect_metadata

                # 메타데이터 수집
                meta = collect_metadata(source)
                self._data["file_metadata"] = meta

                # 암호화 수행
                result = encrypt_file(
                    filepath=source,
                    aes_key=aes_key,
                    output_path=enc_path,
                    chunk_size=chunk_bytes,
                    progress_cb=progress_cb,
                )
                enc_result_holder.append(result)
                return result
            except Exception as exc:
                enc_error_holder.append(exc)
                raise

        def on_complete(result):
            import struct, json
            self._data["enc_path"] = enc_path
            self._data["enc_result"] = result

            # .enc 파일에서 메타데이터 읽기
            try:
                with open(enc_path, "rb") as f:
                    f.seek(-4, 2)
                    meta_size = struct.unpack("<I", f.read(4))[0]
                    f.seek(-4 - meta_size, 2)
                    enc_meta = json.loads(f.read(meta_size).decode("utf-8"))
                self._data["enc_meta"] = enc_meta
            except Exception:
                pass

            self._data["encryption_done"] = True

        def on_error(exc):
            messagebox.showerror(
                t("encrypt.failed_title"),
                f"{t('encrypt.failed_msg')}:\n{exc}",
                parent=self.winfo_toplevel(),
            )

        dlg = ProgressDialog(
            self.winfo_toplevel(),
            title=t("process.encryption_progress_title"),
            task_fn=task_fn,
            on_complete=on_complete,
            on_error=on_error,
        )
        self.winfo_toplevel().wait_window(dlg)

        # 시간 정보 저장
        self._data["encrypt_start_time"] = dlg.start_time_iso
        self._data["encrypt_end_time"] = dlg.end_time_iso
        self._data["encrypt_elapsed"] = dlg.elapsed_seconds

    # --- S2: Seizure / sealing info ---------------------------------------

    def _build_s2(self, parent: tk.Frame) -> None:
        from tkinter import ttk

        tk.Label(
            parent,
            text=t("seal.s2_title"),
            font=get_font("header"),
        ).pack(anchor="w", pady=(0, 12))

        # --- Case info group ---
        case_group = ttk.LabelFrame(parent, text=t("seal.case_info"), padding=(8, 4))
        case_group.pack(fill="x", pady=(0, 8))

        self._case_number = LabeledEntry(case_group, t("seal.case_number"), required=True)
        self._case_number.pack(fill="x", pady=4)

        self._seizure_date = LabeledEntry(case_group, t("seal.seizure_date"), required=True)
        self._seizure_date.set(datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"))
        self._seizure_date.pack(fill="x", pady=4)

        self._seizure_location = LabeledEntry(case_group, t("seal.seizure_location"), required=True)
        self._seizure_location.pack(fill="x", pady=4)

        self._media_manufacturer = LabeledEntry(case_group, t("seal.media_manufacturer"))
        self._media_manufacturer.pack(fill="x", pady=2)

        self._media_model = LabeledEntry(case_group, t("seal.media_model"))
        self._media_model.pack(fill="x", pady=2)

        self._media_serial = LabeledEntry(case_group, t("seal.media_serial"))
        self._media_serial.pack(fill="x", pady=2)

        # --- Investigator info group ---
        inv_group = ttk.LabelFrame(parent, text=t("seal.investigator_info"), padding=(8, 4))
        inv_group.pack(fill="x", pady=(0, 4))

        self._investigator_name = LabeledEntry(inv_group, t("seal.investigator_name"), required=True)
        self._investigator_name.pack(fill="x", pady=4)

        self._investigator_rank = LabeledEntry(inv_group, t("seal.investigator_rank"))
        self._investigator_rank.pack(fill="x", pady=4)

        self._validators.append(self._validate_s2)

    def _validate_s2(self) -> bool:
        fields = [
            self._case_number,
            self._investigator_name,
            self._seizure_date,
            self._seizure_location,
        ]
        errors: list[str] = []
        for f in fields:
            if not f.is_valid():
                f.highlight_error()
                errors.append(t("validate.field_required").format(field=f.label.cget("text").strip("* ")))
            else:
                f.clear_error()
        if errors:
            messagebox.showwarning(
                t("common.input_error"),
                "\n".join(errors),
                parent=self.winfo_toplevel(),
            )
            return False
        self._data["case_number"] = self._case_number.get()
        self._data["investigator"] = {
            "name": self._investigator_name.get(),
            "rank": self._investigator_rank.get(),
        }
        self._data["seizure"] = {
            "datetime": self._seizure_date.get(),
            "location": self._seizure_location.get(),
        }
        self._data["media"] = {
            "manufacturer": self._media_manufacturer.get(),
            "model": self._media_model.get(),
            "serial": self._media_serial.get(),
        }
        return True

    # --- S3: Subject (suspect) info ---------------------------------------

    def _build_s3(self, parent: tk.Frame) -> None:
        from tkinter import ttk

        # Scrollable container for S3 (signature pad may not be visible on small screens)
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Mouse wheel binding for scroll
        def _on_mousewheel(event: tk.Event) -> None:  # type: ignore[type-arg]
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Ensure inner frame stretches to canvas width
        def _stretch_inner(_e: tk.Event) -> None:  # type: ignore[type-arg]
            canvas.itemconfig(canvas.find_withtag("all")[0], width=_e.width)
        canvas.bind("<Configure>", _stretch_inner)

        # Store canvas ref so mousewheel can be unbound when leaving S3
        self._s3_canvas = canvas

        tk.Label(
            scrollable_frame,
            text=t("seal.s3_title"),
            font=get_font("header"),
        ).pack(anchor="w", pady=(0, 12))

        # --- Personal info group ---
        person_group = ttk.LabelFrame(scrollable_frame, text=t("seal.subject_info"), padding=(8, 4))
        person_group.pack(fill="x", pady=(0, 8))

        self._subject_name = LabeledEntry(person_group, t("seal.subject_name"), required=True)
        self._subject_name.pack(fill="x", pady=4)

        self._subject_email = LabeledEntry(person_group, t("seal.subject_email"), required=True)
        self._subject_email.pack(fill="x", pady=4)

        self._subject_birth = DateEntry(person_group, t("seal.subject_dob"), required=True)
        self._subject_birth.pack(fill="x", pady=4)

        self._subject_phone = LabeledEntry(person_group, t("seal.subject_phone"), required=True)
        self._subject_phone.pack(fill="x", pady=4)

        # --- Security info group ---
        security_group = ttk.LabelFrame(scrollable_frame, text=t("seal.security_info"), padding=(8, 4))
        security_group.pack(fill="x", pady=(0, 8))

        self._subject_password = LabeledEntry(
            security_group, t("seal.password"), required=True, show="*"
        )
        self._subject_password.pack(fill="x", pady=4)

        self._subject_password_confirm = LabeledEntry(
            security_group, t("seal.password_confirm"), required=True, show="*"
        )
        self._subject_password_confirm.pack(fill="x", pady=4)

        self._signature_pad = EnhancedSignaturePad(
            security_group, label_text=t("seal.signature"), required=True
        )
        self._signature_pad.pack(fill="x", pady=8)

        self._validators.append(self._validate_s3)

    def _validate_s3(self) -> bool:
        fields = [
            self._subject_name,
            self._subject_email,
            self._subject_birth,
            self._subject_phone,
            self._subject_password,
            self._subject_password_confirm,
        ]
        errors: list[str] = []
        for f in fields:
            if not f.is_valid():
                f.highlight_error()
                errors.append(t("validate.field_required").format(field=f.label.cget("text").strip("* ")))
            else:
                f.clear_error()

        pw = self._subject_password.get()
        pw_confirm = self._subject_password_confirm.get()
        if pw and pw_confirm and pw != pw_confirm:
            errors.append(t("validate.password_mismatch"))

        if not self._signature_pad.is_valid():
            errors.append(t("validate.signature_required"))
            logger.warning(
                "S3 signature validation failed: has_signature=%s, confirmed=%s",
                self._signature_pad._has_signature,
                self._signature_pad._confirmed,
            )

        if errors:
            logger.info("S3 validation errors: %s", errors)
            messagebox.showwarning(
                t("common.input_error"),
                "\n".join(errors),
                parent=self.winfo_toplevel(),
            )
            return False

        self._data["subject"] = {
            "name": self._subject_name.get(),
            "email": self._subject_email.get(),
            "birth": self._subject_birth.get(),
            "phone": self._subject_phone.get(),
            "password": self._subject_password.get(),
        }
        self._data["signature_lines"] = self._signature_pad.get_lines()

        # Set signer info on the enhanced signature pad
        today_str = datetime.now().strftime("%Y-%m-%d")
        self._signature_pad.set_signer_info(
            self._subject_name.get(), today_str,
        )
        self._data["signature_data"] = self._signature_pad.get_signature_data()

        return True

    # --- S4: Seal record preview ------------------------------------------

    def _build_s4(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("seal.s4_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._preview_text = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=22,
        )
        scrollbar = tk.Scrollbar(parent, command=self._preview_text.yview)
        self._preview_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._preview_text.pack(fill="both", expand=True)

        self._validators.append(self._validate_s4)

    def _validate_s4(self) -> bool:
        """S4 -- 검토 통과 시 seal_id 생성 + record_dict 구성."""
        if not self._data.get("record_dict"):
            existing_seal_id = self._data.get("seal_id")
            self._generate_seal_data()
            # 케이스에서 시작한 경우 기존 seal_id 복원
            if existing_seal_id:
                self._data["seal_id"] = existing_seal_id
                # record_dict 내부의 seal_id도 동기화
                record = self._data.get("record_dict")
                if record:
                    record["seal_id"] = existing_seal_id
        return True

    def _generate_seal_data(self) -> None:
        """S4 통과 시 seal_id와 record_dict를 생성하여 _data에 저장."""
        from datetime import datetime, timezone

        try:
            from desktop.record import create_seal_id, build_seal_record
            seal_id = create_seal_id()
        except ImportError:
            import os
            now = datetime.now(timezone.utc)
            rand_hex = os.urandom(3).hex().upper()
            seal_id = f"S-{now.strftime('%Y%m%d')}-{rand_hex}"

        self._data["seal_id"] = seal_id

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subject = self._data.get("subject", {})
        investigator = self._data.get("investigator", {})
        seizure = self._data.get("seizure", {})
        media = self._data.get("media", {})

        record_dict = {
            "seal_id": seal_id,
            "case_info": {
                "case_number": self._data.get("case_number", ""),
                "investigator": investigator.get("name", ""),
                "device_user": media.get("device_user", ""),
                "suspect": subject.get("name", ""),
                "storage_type": media.get("type", ""),
                "storage_info": {
                    "manufacturer": media.get("manufacturer", ""),
                    "model": media.get("model", ""),
                    "serial": media.get("serial", ""),
                },
                "seizure_time": seizure.get("datetime", now_iso),
                "seizure_location": seizure.get("location", ""),
            },
            "process_info": {
                "type": "Sealing",
                "start_time": self._data.get("encrypt_start_time", now_iso),
                "end_time": self._data.get("encrypt_end_time", now_iso),
                "file_count": 1,
                "investigator": investigator.get("name", ""),
                "reason": "",
                "participation": t("seal.participation"),
            },
            "file_info": self._build_file_info(),
            "signer_info": {
                "name": subject.get("name", ""),
                "email": subject.get("email", ""),
                "birth_date": subject.get("birth", ""),
                "phone": subject.get("phone", ""),
                "cert_fingerprint": "",
                "signature_image_hash": "",
            },
            "history": {
                "summary": "S1U0R0",
                "events": [{
                    "id": 1,
                    "seal_type": "Sealing",
                    "start_time": now_iso,
                    "end_time": "",
                    "investigator": investigator.get("name", ""),
                }],
            },
        }
        self._data["record_dict"] = record_dict

    def _build_file_info(self) -> dict:
        """암호화 결과가 있으면 실제 메타데이터로, 없으면 빈 값으로 file_info 구성."""
        from pathlib import Path

        meta = self._data.get("file_metadata")
        enc_meta = self._data.get("enc_meta", {})
        enc_path = self._data.get("enc_path", "")

        def _nt(t_val: str) -> str:
            return t_val.replace("+00:00", "Z") if t_val and t_val.endswith("+00:00") else (t_val or "")

        if meta:
            original_files = [{
                "filename": meta.filename,
                "size": meta.size,
                "md5": meta.md5,
                "sha256": meta.sha256,
                "mtime": _nt(meta.mtime),
                "ctime": _nt(meta.ctime),
                "atime": _nt(meta.atime),
            }]
        else:
            original_files = [{
                "filename": self._data.get("source_file", ""),
                "size": 0, "md5": "", "sha256": "",
                "mtime": "", "ctime": "", "atime": "",
            }]

        if enc_meta and enc_path:
            enc_size = Path(enc_path).stat().st_size if Path(enc_path).exists() else 0
            result_files = [{
                "filename": Path(enc_path).name,
                "size": enc_size,
                "encryption_algo": "AES-256-GCM",
                "enc_ended_time": self._data.get("encrypt_end_time", ""),
                "nonces": enc_meta.get("nonces", []),
                "tags": enc_meta.get("tags", []),
                "chunk_lengths": enc_meta.get("chunk_lengths", []),
            }]
        else:
            result_files = []

        return {
            "original_files": original_files,
            "result_files": result_files,
            "hash_match": True,
            "unknown_files": [],
            "derived_files": [],
        }

    def _refresh_s4_preview(self) -> None:
        """Populate the preview text with collected data."""
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")

        lines = [
            "=" * 50,
            t("preview.seal_title"),
            "=" * 50,
            "",
            t("preview.case_info"),
            t("preview.case_number").format(v=self._data.get('case_number', '')),
            t("preview.investigator_name").format(v=self._data.get('investigator', {}).get('name', '')),
            t("preview.investigator_rank").format(v=self._data.get('investigator', {}).get('rank', '')),
            "",
            t("preview.seizure_info"),
            t("preview.seizure_datetime").format(v=self._data.get('seizure', {}).get('datetime', '')),
            t("preview.seizure_location").format(v=self._data.get('seizure', {}).get('location', '')),
            "",
            t("preview.media_info"),
            t("preview.media_manufacturer").format(v=self._data.get('media', {}).get('manufacturer', '')),
            t("preview.media_model").format(v=self._data.get('media', {}).get('model', '')),
            f"  S/N: {self._data.get('media', {}).get('serial', '')}",
            "",
            t("preview.subject_info"),
            t("preview.subject_name").format(v=self._data.get('subject', {}).get('name', '')),
            t("preview.subject_email").format(v=self._data.get('subject', {}).get('email', '')),
            t("preview.subject_dob").format(v=self._data.get('subject', {}).get('birth', '')),
            t("preview.subject_phone").format(v=self._data.get('subject', {}).get('phone', '')),
            "",
            t("preview.target_file_section"),
            t("preview.file_path").format(v=self._data.get('source_file', '')),
            t("preview.chunk_size").format(v=self._data.get('chunk_size_gb', '')),
            "",
            "=" * 50,
            t("preview.confirm_next"),
        ]
        self._preview_text.insert("1.0", "\n".join(lines))
        self._preview_text.configure(state="disabled")

    # --- S5: Digital signature progress -----------------------------------

    def _build_s5(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("seal.s5_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._s5_status = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=18,
        )
        self._s5_status.pack(fill="both", expand=True, pady=4)

        self._s5_progress_label = tk.Label(
            parent, text=t("seal.s5_waiting"), anchor="w"
        )
        self._s5_progress_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_s5)

    def _validate_s5(self) -> bool:
        """S5 proceeds after signature processing is triggered."""
        return self._data.get("signature_done", False)

    def _update_s5_status(self, message: str) -> None:
        """Append a status line to the S5 status text."""
        self._s5_status.configure(state="normal")
        self._s5_status.insert("end", f"  {message}\n")
        self._s5_status.see("end")
        self._s5_status.configure(state="disabled")

    def _trigger_s5_signing(self) -> None:
        """S5 화면 진입 시 자동으로 전자서명 프로세스를 백그라운드에서 시작."""
        if self._data.get("signature_done"):
            return  # 이미 완료됨

        import threading

        # Cache toplevel reference on main thread (tk.call is not thread-safe)
        _toplevel = self.winfo_toplevel()

        def _status_cb(msg: str) -> None:
            try:
                _toplevel.after(0, self._update_s5_status, msg)
            except RuntimeError:
                logger.debug("Cannot schedule status update (window destroyed?)")

        def _run_signing() -> None:
            try:
                _status_cb(t("seal.sig_process_start"))

                seal_process = self._data.get("_seal_process")
                if seal_process is None:
                    _status_cb(t("seal.sig_no_process"))
                    _run_simple_signing()
                    return

                result = seal_process.run_s5(status_cb=_status_cb)
                self._data["s5_result"] = result
                _status_cb(t("seal.sig_process_done"))

            except Exception as exc:
                _status_cb(t("seal.sig_error_continue").format(v=exc))
            finally:
                try:
                    _toplevel.after(0, self._mark_s5_done)
                except RuntimeError:
                    # Window may be destroyed; mark done directly
                    self._data["signature_done"] = True

        def _run_simple_signing() -> None:
            """SealProcess 없이 간이 서명 수행 (독립 실행 시)."""
            import json
            import hashlib
            from pathlib import Path

            seal_id = self._data.get("seal_id", "S-00000000-000000")
            output_dir = Path(self._data.get("output_dir", "."))
            output_dir.mkdir(parents=True, exist_ok=True)

            # JSON 저장
            record = self._data.get("record_dict", {})
            json_path = str(output_dir / f"{seal_id}_record.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            _status_cb(t("seal.record_json_saved"))

            # PDF placeholder
            pdf_path = str(output_dir / f"{seal_id}_seal_record.pdf")
            try:
                from desktop.record import render_record_pdf
                render_record_pdf(record, "seal_record.html", pdf_path)
                _status_cb(t("seal.pdf_rendered"))
            except Exception as exc:
                _status_cb(t("seal.pdf_fallback").format(v=exc))
                Path(pdf_path).write_text(f"[Placeholder] {seal_id}")

            # 인증서 생성
            try:
                from desktop.signature import (
                    generate_keypair,
                    create_self_signed_cert,
                    save_private_key,
                    save_certificate,
                )
                sig_data = json.dumps(self._data.get("signature_lines", [])).encode()
                sig_hash = hashlib.sha256(sig_data).hexdigest()
                subject = self._data.get("subject", {})
                name = subject.get("name", "Unknown")
                email = subject.get("email", "unknown@example.com")
                pw = subject.get("password", "password")

                private_key, _ = generate_keypair(2048)
                _status_cb(t("seal.rsa_keygen"))

                cert = create_self_signed_cert(private_key, name, email, sig_hash)
                _status_cb(t("seal.x509_cert"))

                cert_path = str(output_dir / f"{seal_id}_cert.pem")
                key_path = str(output_dir / f"{seal_id}_key.pem")
                save_certificate(cert, cert_path)
                save_private_key(private_key, key_path, pw)
                _status_cb(t("seal.cert_saved"))

                self._data["cert_pem_path"] = cert_path
                self._data["key_pem_path"] = key_path
            except Exception as exc:
                _status_cb(t("seal.cert_error").format(v=exc))

            self._data["pdf_path"] = pdf_path
            self._data["record_json_path"] = json_path
            _status_cb(t("seal.sig_process_done"))

        thread = threading.Thread(target=_run_signing, daemon=True)
        thread.start()

    def _mark_s5_done(self) -> None:
        """S5 완료 마킹 — 다음 버튼 활성화."""
        self._data["signature_done"] = True
        self._s5_progress_label.configure(text=t("seal.s5_complete"))

    # --- S6: Key split results + unlock_time ------------------------------

    def _build_s6(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("seal.s6_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._s6_result = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=12,
        )
        self._s6_result.pack(fill="both", expand=True, pady=4)

        unlock_frame = tk.Frame(parent)
        unlock_frame.pack(fill="x", pady=8)
        tk.Label(
            unlock_frame,
            text=t("seal.unlock_label"),
            anchor="w",
            width=20,
        ).pack(side="left")
        self._unlock_days_var = tk.IntVar(value=DEFAULT_UNLOCK_DAYS)
        self._unlock_spin = tk.Spinbox(
            unlock_frame,
            from_=MIN_UNLOCK_DAYS,
            to=MAX_UNLOCK_DAYS,
            textvariable=self._unlock_days_var,
            width=6,
        )
        self._unlock_spin.pack(side="left")
        tk.Label(unlock_frame, text=t("seal.unlock_range")).pack(
            side="left", padx=8
        )

        self._validators.append(self._validate_s6)

    def _validate_s6(self) -> bool:
        try:
            days = self._unlock_days_var.get()
            if not (MIN_UNLOCK_DAYS <= days <= MAX_UNLOCK_DAYS):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showwarning(
                t("common.input_error"),
                t("validate.unlock_range").format(min=MIN_UNLOCK_DAYS, max=MAX_UNLOCK_DAYS),
                parent=self.winfo_toplevel(),
            )
            return False

        now = datetime.now(tz=timezone.utc)
        unlock_time = now + timedelta(days=days)
        self._data["unlock_days"] = days
        self._data["unlock_time_iso"] = unlock_time.isoformat()
        return True

    def _refresh_s6_result(self) -> None:
        """S6 진입 시 키 분할 수행 + 결과 표시."""
        self._s6_result.configure(state="normal")
        self._s6_result.delete("1.0", "end")

        # 키 분할이 아직 안 되어 있으면 수행
        shares = self._data.get("key_shares")
        if not shares or len(shares) != 4:
            shares = self._perform_key_split()

        if shares and len(shares) == 4:
            lines = [
                t("keysplit.complete_title"),
                "",
                t("keysplit.share_subject").format(v=shares[0][:20]),
                t("keysplit.share_investigator").format(v=shares[1][:20]),
                t("keysplit.share_system").format(v=shares[2][:20]),
                t("keysplit.share_admin").format(v=shares[3][:20]),
                "",
                t("keysplit.subject_store"),
                t("keysplit.investigator_store"),
                t("keysplit.system_store"),
            ]
        else:
            lines = [t("keysplit.failed")]

        self._s6_result.insert("1.0", "\n".join(lines))
        self._s6_result.configure(state="disabled")

    def _perform_key_split(self) -> tuple[str, ...] | None:
        """AES 키를 생성하고 SSS(2-of-4)로 분할한다."""
        import os
        try:
            from desktop.crypto import split_key

            # S1에서 실제 암호화된 키가 있으면 사용, 없으면 새로 생성
            aes_key_hex = self._data.get("aes_key_hex")
            if not aes_key_hex:
                aes_key = os.urandom(32)
                aes_key_hex = aes_key.hex()
                self._data["aes_key_hex"] = aes_key_hex

            shares = split_key(aes_key_hex)
            self._data["key_shares"] = shares
            return shares
        except Exception as exc:
            logging.getLogger(__name__).warning("키 분할 실패: %s", exc)
            self._s6_result.insert("end", f"\n{t('common.error')}: {exc}")
            return None

    # --- S7: Completion summary -------------------------------------------

    def _build_s7(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("seal.s7_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._s7_summary = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=20,
        )
        self._s7_summary.pack(fill="both", expand=True, pady=4)

        self._validators.append(self._validate_s7)

    def _validate_s7(self) -> bool:
        """Final step -- always valid."""
        return True

    def _refresh_s7_summary(self) -> None:
        """Populate the final summary with time information."""
        self._s7_summary.configure(state="normal")
        self._s7_summary.delete("1.0", "end")

        seal_id = self._data.get("seal_id", "N/A")
        enc_start = self._data.get("encrypt_start_time", "N/A")
        enc_end = self._data.get("encrypt_end_time", "N/A")
        enc_elapsed = self._data.get("encrypt_elapsed", 0)

        from .progress_dialog import _fmt_time

        meta = self._data.get("file_metadata")
        file_size_str = ""
        if meta:
            gb = meta.size / (1024 ** 3)
            file_size_str = f"{meta.size:,} bytes ({gb:.3f} GB)"

        lines = [
            "=" * 50,
            t("complete.seal_title"),
            "=" * 50,
            "",
            t("complete.seal_info_section"),
            f"  Seal ID       : {seal_id}",
            t("complete.case_number").format(v=self._data.get('case_number', '')),
            t("complete.subject").format(v=self._data.get('subject', {}).get('name', '')),
            t("complete.investigator").format(v=self._data.get('investigator', {}).get('name', '')),
            "",
            t("complete.file_section"),
            t("complete.filename").format(v=self._data.get('source_file', '')),
            t("complete.filesize").format(v=file_size_str),
            t("complete.enc_file").format(v=self._data.get('enc_path', 'N/A')),
            "",
            t("complete.time_section"),
            t("complete.enc_start").format(v=enc_start),
            t("complete.enc_end").format(v=enc_end),
            t("complete.enc_elapsed").format(v=_fmt_time(enc_elapsed)),
            "",
            t("complete.key_section"),
            f"  unlock_time   : {self._data.get('unlock_time_iso', 'N/A')}",
            t("complete.key_shares"),
            "",
            t("complete.notice_section"),
            t("complete.seal_saved"),
            t("complete.key_instruction"),
            "",
            "=" * 50,
        ]
        self._s7_summary.insert("1.0", "\n".join(lines))
        self._s7_summary.configure(state="disabled")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_step(self, index: int) -> None:
        """Display the given step frame and hide others."""
        # Unbind S3 mousewheel when leaving S3
        if hasattr(self, "_s3_canvas") and self._current_step == 2 and index != 2:
            try:
                self._s3_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

        for frame in self._steps:
            frame.pack_forget()
        self._steps[index].pack(fill="both", expand=True)
        self._current_step = index

        # Re-bind S3 mousewheel when entering S3
        if hasattr(self, "_s3_canvas") and index == 2:
            def _on_mousewheel(event: tk.Event) -> None:  # type: ignore[type-arg]
                self._s3_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self._s3_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        step_num = index + 1
        self._step_label.configure(
            text=t("common.step_of").format(current=step_num, total=self.TOTAL_STEPS)
        )
        # S5(digital signature) 이후에는 이전 버튼 비활성화
        if index >= 4 and self._data.get("signature_done"):
            self._prev_btn.configure(state="disabled")
        elif index > 0:
            self._prev_btn.configure(state="normal")
        else:
            self._prev_btn.configure(state="disabled")

        if index == self.TOTAL_STEPS - 1:
            self._next_btn.configure(text=t("common.complete"))
        else:
            self._next_btn.configure(text=t("common.next"))

        # Update step indicator
        self._step_indicator.set_active(index)

        # Refresh dynamic content for specific steps
        if index == 3:
            self._refresh_s4_preview()
        elif index == 4:
            self._trigger_s5_signing()
        elif index == 5:
            self._refresh_s6_result()
        elif index == 6:
            self._refresh_s7_summary()

    def _on_step_click(self, step_index: int) -> None:
        """Handle step indicator click to navigate or view past steps."""
        current = self._current_step

        # Cannot navigate to future steps
        if step_index > current:
            return

        # Same step — no-op
        if step_index == current:
            return

        # After signature, cannot go back — show readonly
        if self._data.get("signature_done") and step_index < 4:
            self._show_step_readonly(step_index)
            return

        # Normal navigation
        self._show_step(step_index)

    def _show_step_readonly(self, index: int) -> None:
        """Show a past step in read-only mode with a 'back to current' button."""
        actual_step = self._current_step
        self._show_step(index)
        # Override nav buttons for readonly viewing
        self._next_btn.configure(
            text=t("common.back_to_current"),
            command=lambda: self._return_to_actual(actual_step),
        )
        self._prev_btn.configure(state="disabled")

    def _return_to_actual(self, actual_step: int) -> None:
        """Return to the actual current step from readonly view."""
        self._next_btn.configure(
            text=t("common.next"),
            command=self._go_next,
        )
        self._show_step(actual_step)

    def _on_return_key(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle Return key — skip if focus is inside an Entry widget."""
        focused = self.winfo_toplevel().focus_get()
        if isinstance(focused, tk.Entry):
            return
        self._go_next()

    def _go_next(self) -> None:
        """Advance to the next step after validation."""
        idx = self._current_step
        print(f"[DEBUG] _go_next called at step {idx} (S{idx+1})")
        validator = self._validators[idx]
        valid = validator()
        print(f"[DEBUG] validator returned: {valid}")
        if not valid:
            if idx == 2:  # S3
                print(f"[DEBUG] S3 sig: has={self._signature_pad._has_signature}, confirmed={self._signature_pad._confirmed}, is_valid={self._signature_pad.is_valid()}")
                for f in [self._subject_name, self._subject_email, self._subject_birth, self._subject_phone, self._subject_password, self._subject_password_confirm]:
                    print(f"[DEBUG]   {f.label.cget('text')}: val='{f.get()}', valid={f.is_valid()}")
            return

        if idx == self.TOTAL_STEPS - 1:
            # Final step -- complete
            if self._on_complete is not None:
                self._on_complete(self._data)
            return

        logger.debug("Advancing from S%d to S%d", idx + 1, idx + 2)
        self._show_step(idx + 1)

    def _go_prev(self) -> None:
        """Return to the previous step."""
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    def _handle_cancel(self) -> None:
        """Confirm cancellation with the user."""
        if messagebox.askyesno(
            t("cancel.title"),
            t("seal.cancel_confirm"),
            parent=self.winfo_toplevel(),
        ):
            if self._on_cancel is not None:
                self._on_cancel()

    # ------------------------------------------------------------------
    # Public API for SealProcess to update wizard state
    # ------------------------------------------------------------------

    def set_data(self, key: str, value: Any) -> None:
        """Set a data value from the orchestration process."""
        self._data[key] = value

    def get_data(self) -> dict[str, Any]:
        """Return a copy of all collected data."""
        return dict(self._data)

    def advance_to(self, step: int) -> None:
        """Programmatically show a given step (0-indexed)."""
        if 0 <= step < self.TOTAL_STEPS:
            self._show_step(step)

    def append_s5_status(self, message: str) -> None:
        """Add a status message to the S5 signature progress view."""
        self._update_s5_status(message)

    def set_s5_complete(self) -> None:
        """Mark S5 as done so the user can proceed."""
        self._data["signature_done"] = True
        self._s5_progress_label.configure(text=t("seal.s5_complete"))
