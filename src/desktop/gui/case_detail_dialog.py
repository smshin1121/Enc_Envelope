"""Case detail dialog with tabbed view for case info, files, history, and artifacts.

Provides three dialog classes:
- CaseDetailDialog: Full tabbed detail view
- ArtifactsWindow: Standalone artifact file list
- HistoryWindow: Standalone history timeline
"""

from __future__ import annotations

import json
import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .i18n import t
from .theme import COLORS, get_color, get_font

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type display names
# ---------------------------------------------------------------------------

def _get_event_label(raw: str) -> str:
    """Return i18n-aware event type label."""
    _map = {
        "seal": "event.seal",
        "Sealing": "event.seal",
        "unseal": "event.unseal",
        "Unsealing": "event.unseal",
        "reseal": "event.reseal",
        "Resealing": "event.reseal",
    }
    key = _map.get(raw)
    return t(key) if key else raw

_ALT_ROW_BG = COLORS["hover"]  # #f8f9fa


# ---------------------------------------------------------------------------
# CaseDetailDialog — tabbed detail view
# ---------------------------------------------------------------------------

class CaseDetailDialog(tk.Toplevel):
    """Tabbed dialog showing full case detail."""

    def __init__(self, parent: tk.Widget, db_path: str, seal_id: str) -> None:
        super().__init__(parent)
        self.title(f"{t('case_detail.title')} — {seal_id}")
        self.geometry("700x520")
        self.minsize(600, 400)
        self.transient(parent)

        self._db_path = db_path
        self._seal_id = seal_id
        self._detail: dict[str, Any] = {}

        self._load_data()
        self._build_ui()
        self.grab_set()

    def _load_data(self) -> None:
        try:
            from desktop.db import get_case_detail
            result = get_case_detail(self._db_path, self._seal_id)
            self._detail = result if result is not None else {}
        except Exception as exc:
            logger.error("상세 조회 실패: %s", exc)
            self._detail = {}

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        notebook.add(self._build_info_tab(notebook), text=t("case_detail.tab_info"))
        notebook.add(self._build_files_tab(notebook), text=t("case_detail.tab_files"))
        notebook.add(self._build_history_tab(notebook), text=t("case_detail.tab_history"))
        notebook.add(self._build_artifacts_tab(notebook), text=t("case_detail.tab_artifacts"))

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(
            btn_frame, text=t("common.close"), command=self.destroy, width=10
        ).pack(side="right")

    # -- Tab 1: Basic info ------------------------------------------------

    def _build_info_tab(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, padx=12, pady=12)
        record = self._detail.get("record", {})
        case_info = record.get("case_info", {})
        process_info = record.get("process_info", {})
        signer_info = record.get("signer_info", {})

        sections: list[tuple[str, list[tuple[str, str]]]] = [
            (t("case_detail.section_case"), [
                (t("case_detail.seal_id"), self._seal_id),
                (t("case_detail.case_number"), case_info.get("case_number", record.get("case_number", ""))),
                (t("case_detail.seizure_time"), case_info.get("seizure_time", "")),
                (t("case_detail.seizure_location"), case_info.get("seizure_location", "")),
                (t("case_detail.storage_type"), case_info.get("storage_type", "")),
            ]),
            (t("case_detail.section_subject"), [
                (t("case_detail.name"), signer_info.get("name", case_info.get("suspect", ""))),
                (t("case_detail.email"), signer_info.get("email", "")),
                (t("case_detail.dob"), signer_info.get("birth_date", "")),
                (t("case_detail.phone"), signer_info.get("phone", "")),
            ]),
            (t("case_detail.section_investigator"), [
                (t("case_detail.investigator"), case_info.get("investigator", process_info.get("investigator", ""))),
                (t("case_detail.process_type"), process_info.get("type", record.get("type", ""))),
                (t("case_detail.start_time"), process_info.get("start_time", "")),
                (t("case_detail.end_time"), process_info.get("end_time", "")),
            ]),
        ]

        row = 0
        for section_title, fields in sections:
            tk.Label(
                frame,
                text=section_title,
                font=get_font("subheader"),
                fg=get_color("primary"),
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
            row += 1
            for label_text, value in fields:
                tk.Label(
                    frame, text=f"{label_text}:", font=get_font("body"), anchor="e", width=14,
                    fg=get_color("text_secondary"),
                ).grid(row=row, column=0, sticky="e", padx=(0, 8), pady=1)
                tk.Label(
                    frame, text=str(value), font=get_font("body"), anchor="w",
                    fg=get_color("text"),
                ).grid(row=row, column=1, sticky="w", pady=1)
                row += 1

        frame.columnconfigure(1, weight=1)
        return frame

    # -- Tab 2: File info -------------------------------------------------

    def _build_files_tab(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, padx=12, pady=12)
        record = self._detail.get("record", {})
        file_info = record.get("file_info", {})

        text = tk.Text(
            frame, wrap="word", font=get_font("mono"), state="normal"
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)

        content = json.dumps(file_info, ensure_ascii=False, indent=2)
        text.insert("1.0", content)
        text.configure(state="disabled")

        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return frame

    # -- Tab 3: History timeline ------------------------------------------

    def _build_history_tab(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, padx=12, pady=12)
        record = self._detail.get("record", {})
        history = record.get("history", {})

        events: list[dict[str, Any]]
        if isinstance(history, dict):
            events = list(history.get("events", []))
        elif isinstance(history, list):
            events = list(history)
        else:
            events = []

        canvas = tk.Canvas(frame, bg=COLORS["card_bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["card_bg"])

        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        if not events:
            tk.Label(inner, text=t("case_detail.no_history"), bg=COLORS["card_bg"]).pack(pady=20)
        else:
            for idx, event in enumerate(events):
                self._render_timeline_item(inner, idx + 1, event)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return frame

    def _render_timeline_item(
        self, parent: tk.Frame, seq: int, event: dict[str, Any]
    ) -> None:
        event_type = event.get("event", event.get("seal_type", ""))
        label = _get_event_label(event_type)
        start = event.get("timestamp", event.get("start_time", ""))
        end = event.get("end_time", "")
        actor = event.get("actor", event.get("investigator", ""))
        reason = event.get("reason", "")

        item = tk.Frame(parent, bg=COLORS["card_bg"], padx=8, pady=4)
        item.pack(fill="x", pady=2)

        # Timeline dot
        dot_frame = tk.Frame(item, bg=COLORS["card_bg"], width=30)
        dot_frame.pack(side="left", fill="y")
        dot_frame.pack_propagate(False)
        color = get_color("info_text") if "seal" in event_type.lower() and "un" not in event_type.lower() else (
            get_color("danger_text") if "un" in event_type.lower() else get_color("success_text")
        )
        tk.Label(dot_frame, text=f"[{seq}]", fg=color, bg=COLORS["card_bg"], font=get_font("small")).pack()

        # Details
        detail_frame = tk.Frame(item, bg=COLORS["card_bg"])
        detail_frame.pack(side="left", fill="x", expand=True)
        time_str = start
        if end:
            time_str = f"{start} ~ {end}"
        tk.Label(
            detail_frame,
            text=f"{label}    {time_str}    {actor}",
            bg=COLORS["card_bg"],
            font=get_font("body"),
            anchor="w",
        ).pack(anchor="w")
        if reason:
            tk.Label(
                detail_frame,
                text=f"  {t('case_detail.reason_prefix')} {reason}",
                bg=COLORS["card_bg"],
                font=get_font("small"),
                fg=get_color("text_secondary"),
                anchor="w",
            ).pack(anchor="w")

        # Separator
        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8)

    # -- Tab 4: Artifacts -------------------------------------------------

    def _build_artifacts_tab(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, padx=12, pady=12)
        try:
            from desktop.db import get_case_artifacts
            artifacts = get_case_artifacts(self._db_path, self._seal_id)
        except Exception:
            artifacts = []

        cols = ("file_name", "file_type", "size", "file_path")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("file_name", text=t("case_detail.col_filename"))
        tree.heading("file_type", text=t("case_detail.col_type"))
        tree.heading("size", text=t("case_detail.col_size"))
        tree.heading("file_path", text=t("case_detail.col_path"))
        tree.column("file_name", width=180)
        tree.column("file_type", width=60)
        tree.column("size", width=80)
        tree.column("file_path", width=300)

        tree.tag_configure("odd", background=_ALT_ROW_BG)
        tree.tag_configure("even", background=COLORS["card_bg"])

        for idx, art in enumerate(artifacts):
            path = art.get("file_path", "")
            name = os.path.basename(path) if path else ""
            size = _format_size(art.get("size_bytes", 0))
            tag = "odd" if idx % 2 == 1 else "even"
            tree.insert(
                "", "end",
                values=(name, art.get("file_type", ""), size, path),
                tags=(tag,),
            )

        tree.bind("<Double-1>", lambda _e: _open_selected_file(tree))
        tree.bind("<Button-3>", lambda e: _show_context_menu(e, tree))

        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return frame


# ---------------------------------------------------------------------------
# ArtifactsWindow -- standalone artifact viewer
# ---------------------------------------------------------------------------

class ArtifactsWindow(tk.Toplevel):
    """Standalone window listing artifacts for a case."""

    def __init__(self, parent: tk.Widget, db_path: str, seal_id: str) -> None:
        super().__init__(parent)
        self.title(f"{t('case_detail.tab_artifacts')} — {seal_id}")
        self.geometry("650x350")
        self.minsize(500, 250)
        self.transient(parent)

        try:
            from desktop.db import get_case_artifacts
            artifacts = get_case_artifacts(db_path, seal_id)
        except Exception:
            artifacts = []

        self._build(artifacts)
        self.grab_set()

    def _build(self, artifacts: list[dict[str, Any]]) -> None:
        cols = ("file_name", "file_type", "size", "file_path")
        tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        tree.heading("file_name", text=t("case_detail.col_filename"))
        tree.heading("file_type", text=t("case_detail.col_type"))
        tree.heading("size", text=t("case_detail.col_size"))
        tree.heading("file_path", text=t("case_detail.col_path"))
        tree.column("file_name", width=180)
        tree.column("file_type", width=60)
        tree.column("size", width=80)
        tree.column("file_path", width=280)

        tree.tag_configure("odd", background=_ALT_ROW_BG)
        tree.tag_configure("even", background=COLORS["card_bg"])

        for idx, art in enumerate(artifacts):
            path = art.get("file_path", "")
            name = os.path.basename(path) if path else ""
            size = _format_size(art.get("size_bytes", 0))
            tag = "odd" if idx % 2 == 1 else "even"
            tree.insert("", "end", values=(name, art.get("file_type", ""), size, path), tags=(tag,))

        tree.bind("<Double-1>", lambda _e: _open_selected_file(tree))
        tree.bind("<Button-3>", lambda e: _show_context_menu(e, tree))

        scroll = ttk.Scrollbar(self, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        scroll.pack(side="right", fill="y", pady=8)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(btn_frame, text=t("common.close"), command=self.destroy, width=10).pack(side="right")


# ---------------------------------------------------------------------------
# HistoryWindow -- standalone history timeline
# ---------------------------------------------------------------------------

class HistoryWindow(tk.Toplevel):
    """Standalone window showing case history timeline."""

    def __init__(self, parent: tk.Widget, db_path: str, seal_id: str) -> None:
        super().__init__(parent)
        self.title(f"{t('case_detail.tab_history')} — {seal_id}")
        self.geometry("600x400")
        self.minsize(500, 300)
        self.transient(parent)

        try:
            from desktop.db import get_case_history
            events = get_case_history(db_path, seal_id)
        except Exception:
            events = []

        self._build(events)
        self.grab_set()

    def _build(self, events: list[dict[str, Any]]) -> None:
        canvas = tk.Canvas(self, bg=COLORS["card_bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["card_bg"])

        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        if not events:
            tk.Label(inner, text=t("case_detail.no_history"), bg=COLORS["card_bg"], font=get_font("body")).pack(pady=40)
        else:
            for idx, event in enumerate(events):
                self._render_item(inner, idx + 1, event)

        canvas.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        scrollbar.pack(side="right", fill="y", pady=8)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(btn_frame, text=t("common.close"), command=self.destroy, width=10).pack(side="right")

    def _render_item(self, parent: tk.Frame, seq: int, event: dict[str, Any]) -> None:
        event_type = event.get("event", event.get("seal_type", ""))
        label = _get_event_label(event_type)
        start = event.get("timestamp", event.get("start_time", ""))
        end = event.get("end_time", "")
        actor = event.get("actor", event.get("investigator", ""))
        reason = event.get("reason", "")

        item = tk.Frame(parent, bg=COLORS["card_bg"], padx=12, pady=6)
        item.pack(fill="x")

        color = get_color("info_text")
        if "un" in event_type.lower():
            color = get_color("danger_text")
        elif "re" in event_type.lower():
            color = get_color("success_text")

        header = f"[{seq}] {label}"
        time_str = start
        if end:
            time_str = f"{start} ~ {end}"
        line = f"{header}    {time_str}    {actor}"

        tk.Label(
            item, text=line, bg=COLORS["card_bg"], font=get_font("body"), fg=color, anchor="w"
        ).pack(anchor="w")

        if reason:
            tk.Label(
                item, text=f"    {t('case_detail.reason_prefix')} {reason}", bg=COLORS["card_bg"], font=get_font("small"),
                fg=get_color("text_secondary"), anchor="w",
            ).pack(anchor="w")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=12)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "-"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _open_selected_file(tree: ttk.Treeview) -> None:
    selection = tree.selection()
    if not selection:
        return
    values = tree.item(selection[0], "values")
    if not values or len(values) < 4:
        return
    file_path = values[3]
    if not file_path or not os.path.exists(file_path):
        messagebox.showwarning(t("common.warning"), f"{t('artifact.file_not_found')}:\n{file_path}")
        return
    try:
        os.startfile(file_path)
    except Exception as exc:
        messagebox.showerror(t("common.error"), f"{exc}")


def _show_context_menu(event: tk.Event, tree: ttk.Treeview) -> None:
    """Show right-click context menu for artifact treeview."""
    iid = tree.identify_row(event.y)
    if not iid:
        return
    tree.selection_set(iid)
    values = tree.item(iid, "values")
    if not values or len(values) < 4:
        return
    file_path = values[3]

    menu = tk.Menu(tree, tearoff=0)
    menu.add_command(
        label=t("artifact.open"),
        command=lambda: _try_open(file_path),
    )
    menu.add_command(
        label=t("artifact.open_folder"),
        command=lambda: _try_open_folder(file_path),
    )
    menu.add_command(
        label=t("artifact.copy_path"),
        command=lambda: _copy_to_clipboard(tree, file_path),
    )
    menu.tk_popup(event.x_root, event.y_root)


def _try_open(path: str) -> None:
    if os.path.exists(path):
        try:
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror(t("common.error"), f"{exc}")
    else:
        messagebox.showwarning(t("common.warning"), f"{t('artifact.file_not_found')}: {path}")


def _try_open_folder(path: str) -> None:
    folder = os.path.dirname(path)
    if os.path.isdir(folder):
        try:
            os.startfile(folder)
        except Exception as exc:
            messagebox.showerror(t("common.error"), f"{exc}")
    else:
        messagebox.showwarning(t("common.warning"), f"{t('artifact.folder_not_found')}: {folder}")


def _copy_to_clipboard(widget: tk.Widget, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)
    # update_idletasks() flushes the clipboard without re-entering the
    # event loop (update() risks re-entrancy during event handling)
    widget.update_idletasks()
