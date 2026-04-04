"""Reseal process wizard (R1 through R8).

Guides the investigator through the complete resealing workflow:
R1 - Load previous unseal record JSON
R2 - File comparison results (known / unknown files)
R3 - Unknown file classification UI
R4 - Reseal info input (investigator, reason, participation)
R5 - Encryption progress
R6 - Reseal record preview
R7 - Key split results + unlock_time setting
R8 - Completion summary
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import messagebox
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
MIN_UNLOCK_DAYS = 1
MAX_UNLOCK_DAYS = 30
DEFAULT_UNLOCK_DAYS = 10
MIN_CHUNK_GB = 1
MAX_CHUNK_GB = 64
DEFAULT_CHUNK_GB = 64


class ResealWizard(tk.Frame):
    """Multi-step wizard for the resealing process.

    Each step is built as a separate frame.  Navigation buttons
    (이전 / 다음) control the visible frame.
    """

    TOTAL_STEPS = 8

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
        # Header — dark green background for reseal process
        _reseal_header_bg = "#1e6e3e"
        self._header = tk.Frame(self, bg=_reseal_header_bg, height=56)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        self._title_label = tk.Label(
            self._header,
            text=t("reseal.title"),
            fg="#ffffff",
            bg=_reseal_header_bg,
            font=get_font("title"),
        )
        self._title_label.pack(side="left", padx=16)
        self._step_label = tk.Label(
            self._header,
            text="",
            fg="#a8e0c0",
            bg=_reseal_header_bg,
            font=get_font("body"),
        )
        self._step_label.pack(side="right", padx=16)

        # Step indicator with subtle background
        step_bg_frame = tk.Frame(self, bg=get_color("step_bg"))
        step_bg_frame.pack(fill="x")
        self._step_indicator = StepIndicator(step_bg_frame, steps=[
            t("reseal.step1"), t("reseal.step2"), t("reseal.step3"),
            t("reseal.step4"), t("reseal.step5"), t("reseal.step6"),
            t("reseal.step7"), t("reseal.step8"),
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
        """Build all 8 step frames."""
        builders = [
            self._build_r1,
            self._build_r2,
            self._build_r3,
            self._build_r4,
            self._build_r5,
            self._build_r6,
            self._build_r7,
            self._build_r8,
        ]
        for builder in builders:
            frame = tk.Frame(self._content)
            builder(frame)
            self._steps.append(frame)

    def _apply_prefill(self) -> None:
        """Apply prefill data from case workflow to R1 fields."""
        pf = self._prefill_data
        if not pf:
            return

        # Previous unseal record JSON path (derived from pdf_path)
        pdf_path = pf.get("pdf_path", "")
        seal_id = pf.get("seal_id", "")
        if pdf_path and seal_id and hasattr(self, "_prev_record_selector"):
            from pathlib import Path
            # The unseal record JSON is typically at:
            # <dir>/<seal_id>_record.json
            record_json_path = pf.get("record_json_path", "")
            if record_json_path:
                self._prev_record_selector.set(record_json_path)

        if seal_id:
            self._data["seal_id"] = seal_id

    # --- R1: Load previous record ----------------------------------------

    def _build_r1(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r1_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._prev_record_selector = FileSelector(
            parent,
            t("reseal.prev_record"),
            required=True,
            filetypes=[
                (t("filedialog.json_files"), "*.json"),
                (t("filedialog.all_files"), "*.*"),
            ],
        )
        self._prev_record_selector.pack(fill="x", pady=4)

        # Target directory for resealing
        self._target_dir_selector = FileSelector(
            parent,
            t("reseal.target_dir"),
            select_dir=True,
            required=True,
        )
        self._target_dir_selector.pack(fill="x", pady=4)

        # Output directory
        self._output_dir_selector = FileSelector(
            parent,
            t("reseal.output_dir"),
            select_dir=True,
            required=True,
        )
        self._output_dir_selector.pack(fill="x", pady=4)

        # Preview area for loaded record
        tk.Label(
            parent,
            text=t("reseal.loaded_info"),
            font=("맑은 고딕", 10, "bold"),
        ).pack(anchor="w", pady=(12, 4))

        self._r1_info = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=10,
        )
        self._r1_info.pack(fill="both", expand=True, pady=4)

        self._validators.append(self._validate_r1)

    def _validate_r1(self) -> bool:
        errors: list[str] = []
        if not self._prev_record_selector.is_valid():
            errors.append(t("validate.prev_record"))
        if not self._target_dir_selector.is_valid():
            errors.append(t("validate.target_dir"))
        if not self._output_dir_selector.is_valid():
            errors.append(t("validate.select_output"))

        if errors:
            messagebox.showwarning(t("common.input_error"), "\n".join(errors))
            return False

        # Load and validate via process
        try:
            from ..reseal_process import ResealProcess

            process = ResealProcess(db_path=self._app.db_path)
            record_path = self._prev_record_selector.get()
            result = process.run_r1_load(record_path)

            self._data["_process"] = process
            self._data["prev_record"] = result["prev_record"]
            self._data["seal_id"] = result["seal_id"]
            self._data["target_dir"] = self._target_dir_selector.get()
            self._data["output_dir"] = self._output_dir_selector.get()

            # Show loaded info
            self._show_r1_info(result["prev_record"])
            return True

        except Exception as exc:
            messagebox.showerror(t("reseal.record_error"), str(exc))
            return False

    def _show_r1_info(self, record: dict[str, Any]) -> None:
        """Display loaded record summary."""
        self._r1_info.configure(state="normal")
        self._r1_info.delete("1.0", "end")
        lines = [
            f"  Seal ID: {record.get('seal_id', 'N/A')}",
            t("reseal.record_info_type").format(v=record.get('type', 'N/A')),
            t("reseal.record_info_case").format(v=record.get('case_number', 'N/A')),
            t("reseal.record_info_created").format(v=record.get('created_at', 'N/A')),
            f"  Summary: {record.get('summary', 'N/A')}",
        ]
        self._r1_info.insert("1.0", "\n".join(lines))
        self._r1_info.configure(state="disabled")

    # --- R2: File comparison results -------------------------------------

    def _build_r2(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r2_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        # Known files section
        tk.Label(
            parent, text=t("reseal.known_files"), font=("맑은 고딕", 10, "bold")
        ).pack(anchor="w", pady=(4, 2))

        self._r2_known_list = tk.Listbox(parent, height=6, font=("맑은 고딕", 9))
        self._r2_known_list.pack(fill="x", pady=2)

        # Unknown files section
        tk.Label(
            parent,
            text=t("reseal.unknown_files"),
            font=("맑은 고딕", 10, "bold"),
            fg=get_color("danger"),
        ).pack(anchor="w", pady=(8, 2))

        self._r2_unknown_list = tk.Listbox(
            parent, height=6, font=("맑은 고딕", 9)
        )
        self._r2_unknown_list.pack(fill="x", pady=2)

        self._r2_summary_label = tk.Label(
            parent, text="", anchor="w", font=("맑은 고딕", 9)
        )
        self._r2_summary_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_r2)

    def _validate_r2(self) -> bool:
        """R2 always passes."""
        return True

    def _refresh_r2(self) -> None:
        """Run file comparison and display results."""
        process = self._data.get("_process")
        if process is None:
            return

        try:
            result = process.run_r2_compare(self._data["target_dir"])
            self._data["known_files"] = result["known_files"]
            self._data["unknown_files"] = result["unknown_files"]
        except Exception as exc:
            logger.warning("R2 파일 비교 오류: %s", exc)
            self._data["known_files"] = []
            self._data["unknown_files"] = []

        # Populate known files list
        self._r2_known_list.delete(0, "end")
        for kf in self._data.get("known_files", []):
            name = kf.get("filename", "")
            size = kf.get("size", 0)
            self._r2_known_list.insert("end", f"{name}  ({size:,} bytes)")

        # Populate unknown files list
        self._r2_unknown_list.delete(0, "end")
        for uf in self._data.get("unknown_files", []):
            name = uf.get("filename", "")
            size = uf.get("size", 0)
            cat = uf.get("suggested_category", "")
            self._r2_unknown_list.insert(
                "end", f"{name}  ({size:,} bytes) [{cat}]"
            )

        known_count = len(self._data.get("known_files", []))
        unknown_count = len(self._data.get("unknown_files", []))
        self._r2_summary_label.configure(
            text=t("reseal.file_summary").format(known=known_count, unknown=unknown_count)
        )

    # --- R3: Unknown file classification ---------------------------------

    def _build_r3(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r3_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            parent,
            text=t("reseal.r3_guide"),
            font=("맑은 고딕", 9),
            fg=get_color("text_secondary"),
        ).pack(anchor="w", pady=(0, 8))

        # Scrollable classification area
        self._r3_canvas = tk.Canvas(parent, height=280)
        self._r3_scrollbar = tk.Scrollbar(
            parent, orient="vertical", command=self._r3_canvas.yview
        )
        self._r3_inner_frame = tk.Frame(self._r3_canvas)

        self._r3_inner_frame.bind(
            "<Configure>",
            lambda e: self._r3_canvas.configure(
                scrollregion=self._r3_canvas.bbox("all")
            ),
        )
        self._r3_canvas.create_window(
            (0, 0), window=self._r3_inner_frame, anchor="nw"
        )
        self._r3_canvas.configure(yscrollcommand=self._r3_scrollbar.set)

        self._r3_scrollbar.pack(side="right", fill="y")
        self._r3_canvas.pack(fill="both", expand=True)

        # Classification entries will be built dynamically
        self._r3_entries: list[dict[str, Any]] = []

        self._r3_status_label = tk.Label(
            parent, text="", anchor="w", fg=get_color("danger")
        )
        self._r3_status_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_r3)

    def _validate_r3(self) -> bool:
        """All unknown files must be classified before proceeding."""
        unknown_files = self._data.get("unknown_files", [])
        if not unknown_files:
            return True

        unclassified = 0
        classifications = []

        for entry in self._r3_entries:
            classification = entry["class_var"].get()
            if not classification:
                unclassified += 1
                continue

            info: dict[str, Any] = {
                "filepath": entry["filepath"],
                "filename": entry["filename"],
                "size": entry["size"],
                "sha256": entry["sha256"],
                "classification": classification,
                "parent_file": "",
                "derivation_reason": "",
            }

            if classification == "derived":
                parent = entry["parent_var"].get().strip()
                reason = entry["reason_var"].get().strip()
                if not parent:
                    unclassified += 1
                    continue
                info["parent_file"] = parent
                info["derivation_reason"] = reason

            classifications.append(info)

        if unclassified > 0:
            self._r3_status_label.configure(
                text=t("validate.classification_incomplete").format(count=unclassified)
            )
            messagebox.showwarning(
                t("validate.classification_title"),
                t("validate.classification_incomplete").format(count=unclassified),
            )
            return False

        # Store classifications via process
        process = self._data.get("_process")
        if process is not None:
            try:
                from ..reseal_process import UnknownFileInfo

                classified = [
                    UnknownFileInfo(
                        filepath=c["filepath"],
                        filename=c["filename"],
                        size=c["size"],
                        sha256=c["sha256"],
                        suggested_category="",
                        classification=c["classification"],
                        parent_file=c.get("parent_file", ""),
                        derivation_reason=c.get("derivation_reason", ""),
                    )
                    for c in classifications
                ]
                process.set_r3_classifications(classified)
            except Exception as exc:
                logger.warning("R3 분류 저장 오류: %s", exc)

        self._data["classifications"] = classifications
        self._r3_status_label.configure(text="")
        return True

    def _refresh_r3(self) -> None:
        """Build classification UI for each unknown file."""
        # Clear existing entries
        for widget in self._r3_inner_frame.winfo_children():
            widget.destroy()
        self._r3_entries.clear()

        unknown_files = self._data.get("unknown_files", [])

        if not unknown_files:
            tk.Label(
                self._r3_inner_frame,
                text=t("reseal.r3_no_unknown"),
                font=("맑은 고딕", 10),
            ).pack(pady=20)
            return

        for idx, uf in enumerate(unknown_files):
            entry_frame = tk.LabelFrame(
                self._r3_inner_frame,
                text=f"[{idx + 1}] {uf.get('filename', '')}",
                font=("맑은 고딕", 9, "bold"),
                padx=8,
                pady=4,
            )
            entry_frame.pack(fill="x", pady=4, padx=4)

            # File info
            size = uf.get("size", 0)
            cat = uf.get("suggested_category", "uncategorized")
            tk.Label(
                entry_frame,
                text=t("reseal.file_size_cat").format(size=f"{size:,}", cat=cat),
                font=("맑은 고딕", 8),
                fg=get_color("text_secondary"),
            ).pack(anchor="w")

            # Classification radio buttons
            class_var = tk.StringVar(value="")
            radio_frame = tk.Frame(entry_frame)
            radio_frame.pack(fill="x", pady=2)

            tk.Radiobutton(
                radio_frame,
                text=t("reseal.derived"),
                variable=class_var,
                value="derived",
            ).pack(side="left")
            tk.Radiobutton(
                radio_frame,
                text=t("reseal.excluded"),
                variable=class_var,
                value="excluded",
            ).pack(side="left", padx=16)

            # Derived file details (parent + reason)
            detail_frame = tk.Frame(entry_frame)
            detail_frame.pack(fill="x", pady=2)

            parent_var = tk.StringVar()
            tk.Label(detail_frame, text=t("reseal.parent_file"), width=10, anchor="w").pack(
                side="left"
            )
            parent_entry = tk.Entry(
                detail_frame, textvariable=parent_var, width=30
            )
            parent_entry.pack(side="left", padx=4)

            reason_var = tk.StringVar()
            tk.Label(detail_frame, text=t("reseal.derivation_reason"), width=10, anchor="w").pack(
                side="left"
            )
            reason_entry = tk.Entry(
                detail_frame, textvariable=reason_var, width=30
            )
            reason_entry.pack(side="left", padx=4)

            self._r3_entries.append(
                {
                    "filepath": uf.get("filepath", ""),
                    "filename": uf.get("filename", ""),
                    "size": size,
                    "sha256": uf.get("sha256", ""),
                    "class_var": class_var,
                    "parent_var": parent_var,
                    "reason_var": reason_var,
                }
            )

    # --- R4: Reseal info input -------------------------------------------

    def _build_r4(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r4_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._r4_investigator = LabeledEntry(
            parent, t("reseal.investigator"), required=True
        )
        self._r4_investigator.pack(fill="x", pady=4)

        self._r4_reason = LabeledEntry(parent, t("reseal.reason"), required=True)
        self._r4_reason.pack(fill="x", pady=4)

        participation_frame = tk.Frame(parent)
        participation_frame.pack(fill="x", pady=8)
        tk.Label(
            participation_frame, text=t("reseal.participation"), anchor="w", width=20
        ).pack(side="left")
        self._r4_participation_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            participation_frame,
            text=t("reseal.participate"),
            variable=self._r4_participation_var,
        ).pack(side="left")

        # Chunk size
        chunk_frame = tk.Frame(parent)
        chunk_frame.pack(fill="x", pady=8)
        tk.Label(
            chunk_frame, text=f"* {t('reseal.chunk_size')}", anchor="w", width=20
        ).pack(side="left")
        self._r4_chunk_var = tk.IntVar(value=DEFAULT_CHUNK_GB)
        tk.Spinbox(
            chunk_frame,
            from_=MIN_CHUNK_GB,
            to=MAX_CHUNK_GB,
            textvariable=self._r4_chunk_var,
            width=6,
        ).pack(side="left")
        tk.Label(chunk_frame, text=f"GB ({MIN_CHUNK_GB}~{MAX_CHUNK_GB})").pack(
            side="left", padx=8
        )

        self._validators.append(self._validate_r4)

    def _validate_r4(self) -> bool:
        errors: list[str] = []
        if not self._r4_investigator.is_valid():
            self._r4_investigator.highlight_error()
            errors.append(t("validate.investigator_required"))
        else:
            self._r4_investigator.clear_error()

        if not self._r4_reason.is_valid():
            self._r4_reason.highlight_error()
            errors.append(t("validate.reseal_reason"))
        else:
            self._r4_reason.clear_error()

        try:
            chunk = self._r4_chunk_var.get()
            if not (MIN_CHUNK_GB <= chunk <= MAX_CHUNK_GB):
                errors.append(
                    t("validate.chunk_range").format(min=MIN_CHUNK_GB, max=MAX_CHUNK_GB)
                )
        except (tk.TclError, ValueError):
            errors.append(t("validate.chunk_invalid"))

        if errors:
            messagebox.showwarning(t("common.input_error"), "\n".join(errors))
            return False

        self._data["investigator"] = self._r4_investigator.get()
        self._data["reason"] = self._r4_reason.get()
        self._data["subject_participated"] = self._r4_participation_var.get()
        self._data["chunk_size_gb"] = self._r4_chunk_var.get()

        # Set process config
        process = self._data.get("_process")
        if process is not None:
            try:
                from ..reseal_process import ResealConfig

                config = ResealConfig(
                    source_dir=self._data["target_dir"],
                    output_dir=self._data["output_dir"],
                    chunk_size_bytes=self._data["chunk_size_gb"] * (1024 ** 3),
                    investigator=self._data["investigator"],
                    reason=self._data["reason"],
                    subject_participated=self._data["subject_participated"],
                )
                process.set_config(config)
            except Exception as exc:
                logger.warning("R4 config 설정 오류: %s", exc)

        return True

    # --- R5: Encryption progress -----------------------------------------

    def _build_r5(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r5_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._r5_status = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=16,
        )
        self._r5_status.pack(fill="both", expand=True, pady=4)

        self._r5_progress_label = tk.Label(
            parent, text=t("reseal.r5_waiting"), anchor="w"
        )
        self._r5_progress_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_r5)

    def _validate_r5(self) -> bool:
        """R5 proceeds after encryption completes."""
        return self._data.get("encrypt_done", False)

    def _start_r5_encryption(self) -> None:
        """Launch encryption using ProgressDialog."""
        if self._data.get("encrypt_done"):
            return

        process = self._data.get("_process")
        if process is None:
            self._update_r5_status(t("reseal.process_not_init"))
            return

        from .progress_dialog import ProgressDialog

        def task_fn(progress_cb):  # type: ignore[no-untyped-def]
            result = process.run_r5_encrypt(progress_cb=progress_cb)
            return result

        def on_complete(result):  # type: ignore[no-untyped-def]
            enc_count = len(result.get("enc_results", []))
            self._update_r5_status(t("reseal.enc_complete").format(v=enc_count))
            self._data["encrypt_result"] = result
            self._data["encrypt_done"] = True
            self._r5_progress_label.configure(text=t("progress.encrypt_complete"))

            # Generate record (R6)
            try:
                self._update_r5_status(t("reseal.record_generating"))
                record_result = process.run_r6_record()
                self._data["record_result"] = record_result
                self._update_r5_status(t("reseal.record_generated"))
            except Exception as exc:
                logger.warning("R6 기록지 생성 오류: %s", exc)

        def on_error(exc):  # type: ignore[no-untyped-def]
            logger.exception("R5 암호화 오류")
            self._update_r5_status(t("unseal.error_occurred").format(v=exc))
            self._r5_progress_label.configure(text=t("unseal.error_occurred").format(v=exc))

        dlg = ProgressDialog(
            self.winfo_toplevel(),
            title=t("process.reseal_encrypt_title"),
            task_fn=task_fn,
            on_complete=on_complete,
            on_error=on_error,
        )
        self.winfo_toplevel().wait_window(dlg)

    def _update_r5_status(self, message: str) -> None:
        """Append a status line (must be called from main thread)."""
        self._r5_status.configure(state="normal")
        self._r5_status.insert("end", f"  {message}\n")
        self._r5_status.see("end")
        self._r5_status.configure(state="disabled")

    def _update_r5_status_safe(self, message: str) -> None:
        """Thread-safe status update."""
        self.after(0, lambda: self._update_r5_status(message))

    # --- R6: Reseal record preview ---------------------------------------

    def _build_r6(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r6_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._r6_preview = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=22,
        )
        scrollbar = tk.Scrollbar(parent, command=self._r6_preview.yview)
        self._r6_preview.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._r6_preview.pack(fill="both", expand=True)

        self._validators.append(self._validate_r6)

    def _validate_r6(self) -> bool:
        """R6 always passes -- user reviews the content."""
        return True

    def _refresh_r6_preview(self) -> None:
        """Populate the reseal record preview."""
        self._r6_preview.configure(state="normal")
        self._r6_preview.delete("1.0", "end")

        record = self._data.get("record_result", {}).get("record_dict", {})
        seal_id = self._data.get("seal_id", "N/A")

        _yes = t("preview.yes")
        _no = t("preview.no")

        lines = [
            "=" * 50,
            t("preview.reseal_title"),
            "=" * 50,
            "",
            t("preview.case_info"),
            f"  Seal ID: {seal_id}",
            t("preview.case_number").format(v=record.get('case_number', '')),
            "",
            t("preview.reseal_info"),
            t("preview.reason").format(v=self._data.get('reason', '')),
            t("preview.investigator").format(v=self._data.get('investigator', '')),
            t("preview.subject_participated").format(v=_yes if self._data.get('subject_participated') else _no),
            "",
            t("preview.file_status"),
            t("preview.known_files").format(v=len(self._data.get('known_files', []))),
            t("preview.unknown_files").format(v=len(self._data.get('unknown_files', []))),
        ]

        # Show classifications
        classifications = self._data.get("classifications", [])
        derived = [c for c in classifications if c.get("classification") == "derived"]
        excluded = [
            c for c in classifications if c.get("classification") == "excluded"
        ]

        if derived:
            lines.append(t("preview.derived_files").format(v=len(derived)))
            for d in derived:
                lines.append(
                    t("preview.derived_item").format(filename=d['filename'], parent=d.get('parent_file', ''))
                )
        if excluded:
            lines.append(t("preview.excluded_files").format(v=len(excluded)))
            for e in excluded:
                lines.append(f"    - {e['filename']}")

        lines.append("")
        lines.append(t("preview.procedure_history"))
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

        self._r6_preview.insert("1.0", "\n".join(lines))
        self._r6_preview.configure(state="disabled")

    # --- R7: Key split results + unlock_time -----------------------------

    def _build_r7(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r7_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._r7_result = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=12,
        )
        self._r7_result.pack(fill="both", expand=True, pady=4)

        unlock_frame = tk.Frame(parent)
        unlock_frame.pack(fill="x", pady=8)
        tk.Label(
            unlock_frame, text=t("seal.unlock_label"), anchor="w", width=20
        ).pack(side="left")
        self._r7_unlock_days_var = tk.IntVar(value=DEFAULT_UNLOCK_DAYS)
        tk.Spinbox(
            unlock_frame,
            from_=MIN_UNLOCK_DAYS,
            to=MAX_UNLOCK_DAYS,
            textvariable=self._r7_unlock_days_var,
            width=6,
        ).pack(side="left")
        tk.Label(
            unlock_frame, text=t("reseal.unlock_days").format(min=MIN_UNLOCK_DAYS, max=MAX_UNLOCK_DAYS)
        ).pack(side="left", padx=8)

        self._r7_status_label = tk.Label(parent, text="", anchor="w")
        self._r7_status_label.pack(fill="x", pady=4)

        self._validators.append(self._validate_r7)

    def _validate_r7(self) -> bool:
        try:
            days = self._r7_unlock_days_var.get()
            if not (MIN_UNLOCK_DAYS <= days <= MAX_UNLOCK_DAYS):
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showwarning(
                t("common.input_error"),
                t("validate.unlock_range").format(min=MIN_UNLOCK_DAYS, max=MAX_UNLOCK_DAYS),
            )
            return False

        self._data["unlock_days"] = days

        # Run key split
        process = self._data.get("_process")
        if process is not None:
            try:
                result = process.run_r7_split_key(unlock_days=days)
                self._data["key_shares"] = result["shares"]
                self._data["unlock_time_iso"] = result["unlock_time_iso"]
                self._refresh_r7_result()
            except Exception as exc:
                messagebox.showerror(t("keysplit.error"), str(exc))
                return False

        return True

    def _refresh_r7_result(self) -> None:
        """Display key split results."""
        self._r7_result.configure(state="normal")
        self._r7_result.delete("1.0", "end")

        shares = self._data.get("key_shares")
        unlock_time = self._data.get("unlock_time_iso", "N/A")

        if shares and len(shares) == 4:
            lines = [
                t("keysplit.complete_title"),
                "",
                t("keysplit.share_subject").format(v=shares[0][:16]),
                t("keysplit.share_investigator").format(v=shares[1][:16]),
                t("keysplit.share_system").format(v=shares[2][:16]),
                t("keysplit.share_admin").format(v=shares[3][:16]),
                "",
                f"  unlock_time: {unlock_time}",
                "",
                t("keysplit.subject_store"),
                t("keysplit.investigator_store"),
                t("keysplit.system_store"),
            ]
        else:
            lines = [t("keysplit.run_prompt")]

        self._r7_result.insert("1.0", "\n".join(lines))
        self._r7_result.configure(state="disabled")

    # --- R8: Completion summary ------------------------------------------

    def _build_r8(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text=t("reseal.r8_title"),
            font=("맑은 고딕", 12, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        self._r8_summary = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=("맑은 고딕", 9),
            height=20,
        )
        self._r8_summary.pack(fill="both", expand=True, pady=4)

        self._validators.append(self._validate_r8)

    def _validate_r8(self) -> bool:
        """Final step -- always valid."""
        return True

    def _refresh_r8_summary(self) -> None:
        """Populate the final summary and save to DB."""
        self._r8_summary.configure(state="normal")
        self._r8_summary.delete("1.0", "end")

        seal_id = self._data.get("seal_id", "N/A")
        record_result = self._data.get("record_result", {})
        encrypt_result = self._data.get("encrypt_result", {})
        enc_results = encrypt_result.get("enc_results", [])

        lines = [
            "=" * 50,
            t("complete.reseal_title"),
            "=" * 50,
            "",
            f"  Seal ID: {seal_id}",
            t("complete.enc_file_count").format(v=len(enc_results)),
        ]

        for er in enc_results:
            lines.append(f"    - {er.get('enc_filepath', '')}")

        lines.extend([
            "",
            t("complete.record_json").format(v=record_result.get('record_json_path', 'N/A')),
            t("complete.record_pdf").format(v=record_result.get('pdf_path', 'N/A')),
            f"  unlock_time: {self._data.get('unlock_time_iso', 'N/A')}",
            "",
            t("complete.reseal_saved"),
            t("complete.reseal_key_instruction"),
            "",
            "=" * 50,
        ])

        self._r8_summary.insert("1.0", "\n".join(lines))
        self._r8_summary.configure(state="disabled")

        # Save to DB (R8)
        self._run_r8_save()

    def _run_r8_save(self) -> None:
        """Run R8 save in background."""
        process = self._data.get("_process")
        if process is None:
            return

        def _save() -> None:
            try:
                result = process.run_r8_save()
                self._data["reseal_result"] = result
                logger.info("R8 저장 완료: %s", result.seal_id)
            except Exception as exc:
                logger.warning("R8 저장 오류: %s", exc)

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
        step_names = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
        self._step_label.configure(
            text=f"{step_names[index]} ({t('common.step_of').format(current=step_num, total=self.TOTAL_STEPS)})"
        )
        # R5(encryption) 이후에는 이전 버튼 비활성화
        if index >= 4 and self._data.get("encrypt_done"):
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
            self._refresh_r2()
        elif index == 2:
            self._refresh_r3()
        elif index == 4:
            self._start_r5_encryption()
        elif index == 5:
            self._refresh_r6_preview()
        elif index == 6:
            self._refresh_r7_result()
        elif index == 7:
            self._refresh_r8_summary()

    def _on_step_click(self, step_index: int) -> None:
        """Handle step indicator click to navigate or view past steps."""
        current = self._current_step
        if step_index > current:
            return
        if step_index == current:
            return
        if self._data.get("encrypt_done") and step_index < 4:
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
        if messagebox.askyesno(t("cancel.title"), t("reseal.cancel_confirm")):
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
