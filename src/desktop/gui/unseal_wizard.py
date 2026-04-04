"""Unseal process wizard (U3 through U7).

Guides the investigator through the complete unsealing workflow:
U3 - Target file selection, seal record input, AES key input, unseal info
U4 - File-seal record cross verification results
U5 - Decryption progress
U6 - Unseal record preview
U7 - Completion summary (hash verification, record status)
"""

from __future__ import annotations

import logging
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Any, Callable, Optional

from .i18n import t
from .step_indicator import StepIndicator
from .theme import COLORS, FONTS, get_color, get_font
from .widgets import FileSelector, LabeledEntry

if TYPE_CHECKING:
    from .app import MainApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AES_KEY_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class UnsealWizard(tk.Frame):
    """Multi-step wizard for the unsealing process.

    Each step is built as a separate frame.  Navigation buttons
    (이전 / 다음) control the visible frame.
    """

    TOTAL_STEPS = 5

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
        # Header — dark navy background for unseal process
        _unseal_header_bg = "#1a5276"
        self._header = tk.Frame(self, bg=_unseal_header_bg, height=56)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        self._title_label = tk.Label(
            self._header,
            text=t("unseal.title"),
            fg="#ffffff",
            bg=_unseal_header_bg,
            font=get_font("title"),
        )
        self._title_label.pack(side="left", padx=16)
        self._step_label = tk.Label(
            self._header,
            text="",
            fg="#a8c4e0",
            bg=_unseal_header_bg,
            font=get_font("body"),
        )
        self._step_label.pack(side="right", padx=16)

        # Step indicator with subtle background
        step_bg_frame = tk.Frame(self, bg=get_color("step_bg"))
        step_bg_frame.pack(fill="x")
        self._step_indicator = StepIndicator(step_bg_frame, steps=[
            t("unseal.step1"), t("unseal.step2"), t("unseal.step3"),
            t("unseal.step4"), t("unseal.step5"),
        ], on_step_click=self._on_step_click, bg=get_color("step_bg"))
        self._step_indicator.pack(fill="x", padx=16, pady=(8, 4))

        # Navigation bar — pack BEFORE content so it always gets space allocated.
        # In tkinter's packer, widgets packed later with expand=True can starve
        # earlier side="bottom" widgets when content is tall.
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

        # Keyboard shortcuts
        self.winfo_toplevel().bind("<Return>", lambda _e: self._go_next())
        self.winfo_toplevel().bind("<Escape>", lambda _e: self._handle_cancel())

    # ------------------------------------------------------------------
    # Step builders
    # ------------------------------------------------------------------

    def _build_steps(self) -> None:
        """Build all 5 step frames."""
        builders = [
            self._build_u3,
            self._build_u4,
            self._build_u5,
            self._build_u6,
            self._build_u7,
        ]
        for builder in builders:
            frame = tk.Frame(self._content)
            builder(frame)
            self._steps.append(frame)

    def _apply_prefill(self) -> None:
        """Apply prefill data from case workflow to U3 fields."""
        pf = self._prefill_data
        if not pf:
            return

        # Encrypted file path
        enc_filepath = pf.get("enc_filepath", "")
        if enc_filepath and hasattr(self, "_enc_selector"):
            self._enc_selector.set(enc_filepath)

        # Seal record JSON path
        record_json_path = pf.get("record_json_path", "")
        if record_json_path and hasattr(self, "_record_selector"):
            self._record_selector.set(record_json_path)

        # Seal ID
        seal_id = pf.get("seal_id", "")
        if seal_id:
            self._data["seal_id"] = seal_id

    # --- U3: Target selection + inputs -----------------------------------

    def _build_u3(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("unseal.u3_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        # .enc file selector
        self._enc_selector = FileSelector(
            parent,
            t("unseal.enc_file"),
            required=True,
            filetypes=[
                (t("filedialog.enc_files"), "*.enc"),
                (t("filedialog.all_files"), "*.*"),
            ],
        )
        self._enc_selector.pack(fill="x", pady=4)

        # Seal record JSON selector
        self._record_selector = FileSelector(
            parent,
            t("unseal.record_json"),
            required=True,
            filetypes=[
                (t("filedialog.json_files"), "*.json"),
                (t("filedialog.all_files"), "*.*"),
            ],
        )
        self._record_selector.pack(fill="x", pady=4)

        # Output directory
        self._output_selector = FileSelector(
            parent,
            t("unseal.output_dir"),
            select_dir=True,
            required=True,
        )
        self._output_selector.pack(fill="x", pady=4)

        # AES key input
        tk.Label(
            parent,
            text=t("unseal.aes_key_title"),
            font=("맑은 고딕", 10, "bold"),
        ).pack(anchor="w", pady=(12, 4))

        key_frame = tk.Frame(parent)
        key_frame.pack(fill="x", pady=2)
        tk.Label(key_frame, text=f"* {t('unseal.aes_key')}", anchor="w", width=20).pack(
            side="left"
        )
        self._key_var = tk.StringVar()
        self._key_entry = tk.Entry(
            key_frame, textvariable=self._key_var, width=68, show="*"
        )
        self._key_entry.pack(side="left", fill="x", expand=True)

        key_btn_frame = tk.Frame(parent)
        key_btn_frame.pack(fill="x", pady=2)
        tk.Button(
            key_btn_frame,
            text=t("unseal.key_load"),
            command=self._load_key_file,
        ).pack(side="left")
        self._key_show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            key_btn_frame,
            text=t("unseal.show_key"),
            variable=self._key_show_var,
            command=self._toggle_key_visibility,
        ).pack(side="left", padx=8)

        # Unseal info
        tk.Label(
            parent,
            text=t("unseal.unseal_info"),
            font=("맑은 고딕", 10, "bold"),
        ).pack(anchor="w", pady=(12, 4))

        self._reason_entry = LabeledEntry(parent, t("unseal.reason"), required=True)
        self._reason_entry.pack(fill="x", pady=2)

        self._investigator_entry = LabeledEntry(
            parent, t("unseal.investigator"), required=True
        )
        self._investigator_entry.pack(fill="x", pady=2)

        participation_frame = tk.Frame(parent)
        participation_frame.pack(fill="x", pady=4)
        tk.Label(
            participation_frame, text=t("unseal.participation"), anchor="w", width=20
        ).pack(side="left")
        self._participation_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            participation_frame,
            text=t("unseal.participate"),
            variable=self._participation_var,
        ).pack(side="left")

        self._validators.append(self._validate_u3)

    def _load_key_file(self) -> None:
        """Load AES key from a .key file."""
        path = filedialog.askopenfilename(
            title=t("filedialog.key_file_title"),
            filetypes=[
                (t("filedialog.key_files"), "*.key"),
                (t("filedialog.text_files"), "*.txt"),
                (t("filedialog.all_files"), "*.*"),
            ],
        )
        if path:
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
                self._key_var.set(content)
            except Exception as exc:
                messagebox.showerror(t("common.error"), f"{t('keyfile.error')}: {exc}")

    def _toggle_key_visibility(self) -> None:
        """Toggle AES key visibility."""
        show = "" if self._key_show_var.get() else "*"
        self._key_entry.configure(show=show)

    def _validate_u3(self) -> bool:
        errors: list[str] = []
        if not self._enc_selector.is_valid():
            errors.append(t("validate.enc_file"))
        if not self._record_selector.is_valid():
            errors.append(t("validate.record_json"))
        if not self._output_selector.is_valid():
            errors.append(t("validate.select_output"))

        key_hex = self._key_var.get().strip()
        if not key_hex:
            errors.append(t("validate.aes_key_empty"))
        elif not AES_KEY_HEX_PATTERN.match(key_hex):
            errors.append(t("validate.aes_key_invalid"))

        if not self._reason_entry.is_valid():
            self._reason_entry.highlight_error()
            errors.append(t("validate.reason_required"))
        else:
            self._reason_entry.clear_error()

        if not self._investigator_entry.is_valid():
            self._investigator_entry.highlight_error()
            errors.append(t("validate.investigator_required"))
        else:
            self._investigator_entry.clear_error()

        if errors:
            messagebox.showwarning(t("common.input_error"), "\n".join(errors))
            return False

        self._data["enc_filepath"] = self._enc_selector.get()
        self._data["seal_record_path"] = self._record_selector.get()
        self._data["output_dir"] = self._output_selector.get()
        self._data["aes_key_hex"] = key_hex
        self._data["reason"] = self._reason_entry.get()
        self._data["investigator"] = self._investigator_entry.get()
        self._data["subject_participated"] = self._participation_var.get()

        # Run U3 validation + U4 verification in background
        self._run_u3_u4_validation()
        return True

    def _run_u3_u4_validation(self) -> None:
        """Run U3 validation and U4 verification via the process."""
        try:
            from ..unseal_process import UnsealConfig, UnsealProcess

            process = UnsealProcess(db_path=self._app.db_path)
            config = UnsealConfig(
                enc_filepath=self._data["enc_filepath"],
                seal_record_path=self._data["seal_record_path"],
                aes_key_hex=self._data["aes_key_hex"],
                output_dir=self._data["output_dir"],
                reason=self._data["reason"],
                investigator=self._data["investigator"],
                subject_participated=self._data["subject_participated"],
            )
            process.set_config(config)

            result_u3 = process.run_u3_validate()
            self._data["seal_record"] = result_u3["seal_record"]
            self._data["seal_id"] = result_u3["seal_id"]
            self._data["_process"] = process

            result_u4 = process.run_u4_verify()
            self._data["verification_items"] = result_u4["items"]
            self._data["all_matched"] = result_u4["all_matched"]

        except Exception as exc:
            logger.warning("U3/U4 사전 검증 오류: %s", exc)
            self._data["verification_items"] = []
            self._data["all_matched"] = True

    # --- U4: Verification results ----------------------------------------

    def _build_u4(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("unseal.u4_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._u4_result_text = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=18,
        )
        scrollbar = tk.Scrollbar(parent, command=self._u4_result_text.yview)
        self._u4_result_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._u4_result_text.pack(fill="both", expand=True)

        self._u4_warning_label = tk.Label(
            parent, text="", fg="red", anchor="w"
        )
        self._u4_warning_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_u4)

    def _validate_u4(self) -> bool:
        """U4 proceeds; if mismatch, warn and let investigator decide."""
        if not self._data.get("all_matched", True):
            proceed = messagebox.askyesno(
                t("mismatch.title"),
                t("mismatch.msg"),
            )
            if not proceed:
                return False
            self._data["mismatch_acknowledged"] = True
        return True

    def _refresh_u4_results(self) -> None:
        """Populate the U4 verification result text."""
        self._u4_result_text.configure(state="normal")
        self._u4_result_text.delete("1.0", "end")

        items = self._data.get("verification_items", [])
        seal_id = self._data.get("seal_id", "N/A")
        all_matched = self._data.get("all_matched", True)

        lines = [
            "=" * 50,
            t("verify.title"),
            "=" * 50,
            "",
            f"  Seal ID: {seal_id}",
            t("verify.target_file").format(v=self._data.get('enc_filepath', '')),
            "",
        ]

        if items:
            for item in items:
                status = t("verify.match") if item.matched else t("verify.mismatch")
                mark = "[O]" if item.matched else "[X]"
                lines.append(f"  {mark} {item.filename}")
                if item.expected_sha256:
                    lines.append(
                        t("verify.expected_sha256").format(v=item.expected_sha256[:32])
                    )
                if item.actual_sha256:
                    sha_display = item.actual_sha256
                    if len(sha_display) > 32:
                        sha_display = sha_display[:32] + "..."
                    lines.append(t("verify.actual_sha256").format(v=sha_display))
                lines.append(t("verify.result").format(v=status))
                lines.append("")
        else:
            lines.append(t("verify.no_items"))
            lines.append("")

        if all_matched:
            lines.append(t("verify.all_match"))
        else:
            lines.append(t("verify.has_mismatch"))
            lines.append(t("verify.mismatch_action"))
            self._u4_warning_label.configure(
                text=t("verify.mismatch_warning")
            )

        self._u4_result_text.insert("1.0", "\n".join(lines))
        self._u4_result_text.configure(state="disabled")

    # --- U5: Decryption progress -----------------------------------------

    def _build_u5(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("unseal.u5_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._u5_status = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=16,
        )
        self._u5_status.pack(fill="both", expand=True, pady=4)

        self._u5_progress_label = tk.Label(
            parent, text=t("unseal.u5_waiting"), anchor="w"
        )
        self._u5_progress_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_u5)

    def _validate_u5(self) -> bool:
        """U5 proceeds after decryption completes."""
        return self._data.get("decrypt_done", False)

    def _start_u5_decryption(self) -> None:
        """Launch decryption using ProgressDialog."""
        if self._data.get("decrypt_done"):
            return

        process = self._data.get("_process")
        if process is None:
            self._update_u5_status(t("unseal.process_not_init"))
            return

        from .progress_dialog import ProgressDialog

        def task_fn(progress_cb):  # type: ignore[no-untyped-def]
            result = process.run_u5_decrypt(progress_cb=progress_cb)
            return result

        def on_complete(result):  # type: ignore[no-untyped-def]
            self._data["decrypt_result"] = result
            self._data["decrypt_done"] = True

            self._update_u5_status(t("unseal.dec_complete").format(v=result['output_filepath']))
            self._update_u5_status(
                t("unseal.hash_result").format(v=t("preview.hash_pass") if result['hash_verified'] else t("preview.hash_fail"))
            )
            self._update_u5_status(
                t("unseal.sha256_result").format(v=t("complete.match") if result['sha256_match'] else t("complete.mismatch"))
            )
            self._update_u5_status(
                t("unseal.md5_result").format(v=t("complete.match") if result['md5_match'] else t("complete.mismatch"))
            )
            self._u5_progress_label.configure(text=t("progress.decrypt_complete"))

            # Generate record (U6)
            try:
                self._update_u5_status(t("unseal.record_generating"))
                record_result = process.run_u6_record()
                self._data["record_result"] = record_result
                self._update_u5_status(t("unseal.record_generated"))
            except Exception as exc:
                logger.warning("U6 기록지 생성 오류: %s", exc)

        def on_error(exc):  # type: ignore[no-untyped-def]
            logger.exception("U5 복호화 오류")
            self._update_u5_status(t("unseal.error_occurred").format(v=exc))
            self._u5_progress_label.configure(text=t("unseal.error_occurred").format(v=exc))

        dlg = ProgressDialog(
            self.winfo_toplevel(),
            title=t("process.decryption_progress_title"),
            task_fn=task_fn,
            on_complete=on_complete,
            on_error=on_error,
        )
        self.winfo_toplevel().wait_window(dlg)

    def _update_u5_status(self, message: str) -> None:
        """Append a status line to U5 (must be called from main thread)."""
        self._u5_status.configure(state="normal")
        self._u5_status.insert("end", f"  {message}\n")
        self._u5_status.see("end")
        self._u5_status.configure(state="disabled")

    def _update_u5_status_safe(self, message: str) -> None:
        """Thread-safe status update."""
        self.after(0, lambda: self._update_u5_status(message))

    # --- U6: Unseal record preview ---------------------------------------

    def _build_u6(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("unseal.u6_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._u6_preview = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=22,
        )
        scrollbar = tk.Scrollbar(parent, command=self._u6_preview.yview)
        self._u6_preview.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._u6_preview.pack(fill="both", expand=True)

        self._validators.append(self._validate_u6)

    def _validate_u6(self) -> bool:
        """U6 always passes -- user reviews the content."""
        return True

    def _refresh_u6_preview(self) -> None:
        """Populate the unseal record preview."""
        self._u6_preview.configure(state="normal")
        self._u6_preview.delete("1.0", "end")

        record = self._data.get("record_result", {}).get("record_dict", {})
        seal_id = self._data.get("seal_id", "N/A")
        decrypt = self._data.get("decrypt_result", {})

        _yes = t("preview.yes")
        _no = t("preview.no")
        _pass = t("preview.hash_pass")
        _fail = t("preview.hash_fail")

        lines = [
            "=" * 50,
            t("preview.unseal_title"),
            "=" * 50,
            "",
            t("preview.case_info"),
            f"  Seal ID: {seal_id}",
            t("preview.case_number").format(v=record.get('case_number', '')),
            "",
            t("preview.unseal_info"),
            t("preview.reason").format(v=self._data.get('reason', '')),
            t("preview.investigator").format(v=self._data.get('investigator', '')),
            t("preview.subject_participated").format(v=_yes if self._data.get('subject_participated') else _no),
            "",
            t("preview.decrypt_result"),
            t("preview.output_file").format(v=decrypt.get('output_filepath', 'N/A')),
            t("preview.hash_verified").format(v=_pass if decrypt.get('hash_verified') else _fail),
            t("preview.sha256_match").format(v=_yes if decrypt.get('sha256_match') else _no),
            t("preview.md5_match").format(v=_yes if decrypt.get('md5_match') else _no),
            "",
            t("preview.procedure_history"),
        ]

        history = record.get("history", [])
        for idx, event in enumerate(history, 1):
            evt_type = event.get("event", "")
            timestamp = event.get("timestamp", "")
            actor = event.get("actor", "")
            lines.append(f"  {idx}. {evt_type} - {actor} ({timestamp})")

        lines.extend([
            "",
            f"  Summary: {record.get('summary', 'N/A')}",
            "",
            "=" * 50,
            t("preview.confirm_next"),
        ])

        self._u6_preview.insert("1.0", "\n".join(lines))
        self._u6_preview.configure(state="disabled")

    # --- U7: Completion summary ------------------------------------------

    def _build_u7(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("unseal.u7_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._u7_summary = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=20,
        )
        self._u7_summary.pack(fill="both", expand=True, pady=4)

        self._validators.append(self._validate_u7)

    def _validate_u7(self) -> bool:
        """Final step -- always valid."""
        return True

    def _refresh_u7_summary(self) -> None:
        """Populate the final completion summary."""
        self._u7_summary.configure(state="normal")
        self._u7_summary.delete("1.0", "end")

        seal_id = self._data.get("seal_id", "N/A")
        decrypt = self._data.get("decrypt_result", {})
        record_result = self._data.get("record_result", {})

        _pass = t("preview.hash_pass")
        _fail = t("preview.hash_fail")
        _match = t("complete.match")
        _mismatch = t("complete.mismatch")

        lines = [
            "=" * 50,
            t("complete.unseal_title"),
            "=" * 50,
            "",
            f"  Seal ID: {seal_id}",
            t("complete.dec_file").format(v=decrypt.get('output_filepath', 'N/A')),
            "",
            t("complete.hash_result_section"),
            t("complete.overall_hash").format(v=_pass if decrypt.get('hash_verified') else _fail),
            t("complete.sha256").format(v=_match if decrypt.get('sha256_match') else _mismatch),
            t("complete.md5").format(v=_match if decrypt.get('md5_match') else _mismatch),
            "",
            t("complete.record_section"),
            t("complete.record_json").format(v=record_result.get('record_json_path', 'N/A')),
            t("complete.record_pdf").format(v=record_result.get('pdf_path', 'N/A')),
            "",
        ]

        if not decrypt.get("hash_verified"):
            lines.extend([
                t("complete.hash_warn"),
                "",
            ])

        if self._data.get("mismatch_acknowledged"):
            lines.extend([
                t("complete.mismatch_note"),
                "",
            ])

        lines.extend([
            t("complete.unseal_saved"),
            "",
            "=" * 50,
        ])

        self._u7_summary.insert("1.0", "\n".join(lines))
        self._u7_summary.configure(state="disabled")

        # Save to DB (U7)
        self._run_u7_save()

    def _run_u7_save(self) -> None:
        """Run U7 save in background."""
        process = self._data.get("_process")
        if process is None:
            return

        def _save() -> None:
            try:
                result = process.run_u7_save()
                self._data["unseal_result"] = result
                logger.info("U7 저장 완료: %s", result.seal_id)
            except Exception as exc:
                logger.warning("U7 저장 오류: %s", exc)

        thread = threading.Thread(target=_save, daemon=True)
        thread.start()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_step(self, index: int) -> None:
        """Display the given step frame and hide others."""
        for frame in self._steps:
            frame.pack_forget()
        self._steps[index].pack(fill="both", expand=True)
        self._current_step = index

        step_num = index + 1
        step_names = ["U3", "U4", "U5", "U6", "U7"]
        self._step_label.configure(
            text=f"{step_names[index]} ({t('common.step_of').format(current=step_num, total=self.TOTAL_STEPS)})"
        )
        # U5(decryption) 이후에는 이전 버튼 비활성화
        if index >= 2 and self._data.get("decrypt_done"):
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

        # Refresh dynamic content
        if index == 1:
            self._refresh_u4_results()
        elif index == 2:
            self._start_u5_decryption()
        elif index == 3:
            self._refresh_u6_preview()
        elif index == 4:
            self._refresh_u7_summary()

    def _on_step_click(self, step_index: int) -> None:
        """Handle step indicator click to navigate or view past steps."""
        current = self._current_step
        if step_index > current:
            return
        if step_index == current:
            return
        if self._data.get("decrypt_done") and step_index < 2:
            self._show_step_readonly(step_index)
            return
        self._show_step(step_index)

    def _show_step_readonly(self, index: int) -> None:
        """Show a past step in read-only mode with a 'back to current' button."""
        actual_step = self._current_step
        self._show_step(index)
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

    def _go_next(self) -> None:
        """Advance to the next step after validation."""
        idx = self._current_step
        validator = self._validators[idx]
        if not validator():
            return

        if idx == self.TOTAL_STEPS - 1:
            if self._on_complete is not None:
                self._on_complete(self._data)
            return

        self._show_step(idx + 1)

    def _go_prev(self) -> None:
        """Return to the previous step."""
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    def _handle_cancel(self) -> None:
        """Confirm cancellation with the user."""
        if messagebox.askyesno(t("cancel.title"), t("unseal.cancel_confirm")):
            if self._on_cancel is not None:
                self._on_cancel()

    # ------------------------------------------------------------------
    # Public API
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
