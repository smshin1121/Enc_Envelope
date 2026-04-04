"""Home dashboard for the digital evidence electronic sealing system.

Displays statistics, quick-action cards, system status, alerts,
and recent case history in a responsive grid layout.
Styled per DESIGN.md Stripe-inspired design system.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .i18n import t
from .theme import COLORS, FONTS, SPACING, get_color, get_font, get_spacing

logger = logging.getLogger(__name__)

# Process color keys mapped to stat cards
_PROCESS_COLORS = {
    "seal_count": "seal",
    "unseal_count": "unseal",
    "reseal_count": "reseal",
}


class Dashboard(tk.Frame):
    """Home dashboard frame with stats, quick actions, system status, and history."""

    def __init__(self, master: tk.Widget, app: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self._app = app
        self.configure(bg=get_color("bg"))

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=1)

        self._build_title(row=0)
        self._build_stat_cards(row=1)
        self._build_quick_actions(row=2)
        self._build_system_panel(row=1)
        self._build_recent_history(row=3)

        self.refresh()

    # ------------------------------------------------------------------
    # Title — white header with bottom border (DESIGN.md Navigation Header)
    # ------------------------------------------------------------------

    def _build_title(self, row: int) -> None:
        # Deep navy header for strong visual contrast
        _header_bg = "#061b31"
        title_frame = tk.Frame(self, bg=_header_bg)
        title_frame.grid(row=row, column=0, columnspan=2, sticky="ew",
                         padx=0, pady=0)

        inner = tk.Frame(title_frame, bg=_header_bg, height=56)
        inner.pack(fill="x")
        inner.pack_propagate(False)

        tk.Label(
            inner,
            text=t("dashboard.title"),
            font=get_font("title"),
            fg="#ffffff",
            bg=_header_bg,
        ).pack(side="left", padx=get_spacing("lg"), pady=0)

        self._refresh_btn = tk.Button(
            inner,
            text=t("dashboard.refresh"),
            font=get_font("small"),
            command=self.refresh,
            relief="flat",
            bg=_header_bg,
            fg="#a8c4e0",
            activebackground="#0d2a47",
            activeforeground="#ffffff",
            cursor="hand2",
            bd=0,
        )
        self._refresh_btn.pack(side="right", padx=get_spacing("lg"))

    # ------------------------------------------------------------------
    # Stat cards (top row) — large number + label + process color divider
    # ------------------------------------------------------------------

    def _build_stat_cards(self, row: int) -> None:
        self._stat_frame = tk.Frame(self, bg=get_color("bg"))
        self._stat_frame.grid(row=row, column=0, sticky="ew",
                              padx=get_spacing("md"), pady=get_spacing("sm"))

        configs = [
            ("dashboard.seal_count", "seal", "seal_count"),
            ("dashboard.unseal_count", "unseal", "unseal_count"),
            ("dashboard.reseal_count", "reseal", "reseal_count"),
        ]
        self._stat_labels: dict[str, tk.Label] = {}

        for idx, (label_key, color_key, stat_key) in enumerate(configs):
            self._stat_frame.columnconfigure(idx, weight=1)

            process_color = get_color(color_key)

            # Shadow frame (outer) for drop-shadow effect
            shadow = tk.Frame(self._stat_frame, bg=get_color("shadow"))
            shadow.grid(row=0, column=idx, sticky="ew",
                        padx=get_spacing("xs"), pady=get_spacing("xs"))

            # Inner card sits inside shadow with 2px offset (right + bottom)
            card = tk.Frame(shadow, bg=get_color("card_bg"))
            card.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 3))

            # 4px process-color bar at TOP — highly visible
            top_bar = tk.Frame(card, height=4, bg=process_color)
            top_bar.pack(fill="x")

            # Content area with padding
            content = tk.Frame(card, bg=get_color("card_bg"),
                               padx=get_spacing("md"), pady=get_spacing("sm"))
            content.pack(fill="both", expand=True)

            # Large stat number — 40px bold
            num_label = tk.Label(
                content,
                text="0",
                font=get_font("stat_number"),
                fg=get_color("heading"),
                bg=get_color("card_bg"),
            )
            num_label.pack(pady=(get_spacing("sm"), 0))
            self._stat_labels[stat_key] = num_label

            # Label — 10px bold uppercase
            tk.Label(
                content,
                text=t(label_key).upper(),
                font=get_font("stat_label"),
                fg=get_color("text_secondary"),
                bg=get_color("card_bg"),
            ).pack(pady=(0, get_spacing("xs")))

    # ------------------------------------------------------------------
    # Quick action cards (2x2 grid) — white bg, border, hover effects
    # ------------------------------------------------------------------

    def _build_quick_actions(self, row: int) -> None:
        self._action_frame = tk.Frame(self, bg=get_color("bg"))
        self._action_frame.grid(row=row, column=0, sticky="nsew",
                                padx=get_spacing("md"), pady=get_spacing("sm"))
        self._action_frame.columnconfigure(0, weight=1)
        self._action_frame.columnconfigure(1, weight=1)
        self._action_frame.rowconfigure(0, weight=1)
        self._action_frame.rowconfigure(1, weight=1)

        actions = [
            ("dashboard.quick_seal", "dashboard.quick_seal_desc", "seal", self._app._on_seal, 0, 0),
            ("dashboard.quick_unseal", "dashboard.quick_unseal_desc", "unseal", self._app._on_unseal, 0, 1),
            ("dashboard.quick_reseal", "dashboard.quick_reseal_desc", "reseal", self._app._on_reseal, 1, 0),
            ("dashboard.quick_cases", "dashboard.quick_cases_desc", "info", self._app._on_case_manager, 1, 1),
        ]

        for title_key, desc_key, color_key, callback, r, c in actions:
            self._make_action_card(
                self._action_frame, t(title_key), t(desc_key), color_key, callback, r, c,
            )

    def _make_action_card(
        self,
        parent: tk.Widget,
        title: str,
        description: str,
        color_key: str,
        callback: Callable[[], None],
        grid_row: int,
        grid_col: int,
    ) -> None:
        # Color mapping for left bar
        _action_colors = {
            "seal": "#3366cc",
            "unseal": "#1a5276",
            "reseal": "#1e8e4e",
            "info": "#7c3aed",
        }
        bar_color = _action_colors.get(color_key, get_color("primary"))

        # Shadow wrapper
        shadow = tk.Frame(parent, bg=get_color("shadow"), cursor="hand2")
        shadow.grid(row=grid_row, column=grid_col, sticky="nsew",
                    padx=get_spacing("xs"), pady=get_spacing("xs"))

        card = tk.Frame(shadow, bg=get_color("card_bg"))
        card.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))

        # Left 4px color bar
        color_bar = tk.Frame(card, width=4, bg=bar_color)
        color_bar.pack(side="left", fill="y")

        inner = tk.Frame(card, bg=get_color("card_bg"),
                         padx=get_spacing("lg"), pady=get_spacing("lg"))
        inner.pack(fill="both", expand=True)

        title_label = tk.Label(
            inner,
            text=title,
            font=get_font("subheader"),
            fg=get_color(color_key),
            bg=get_color("card_bg"),
            anchor="w",
        )
        title_label.pack(fill="x")

        desc_label = tk.Label(
            inner,
            text=description,
            font=get_font("small"),
            fg=get_color("text_secondary"),
            bg=get_color("card_bg"),
            anchor="w",
        )
        desc_label.pack(fill="x", pady=(get_spacing("sm"), 0))

        hover_bg = get_color("hover")
        normal_bg = get_color("card_bg")

        def _on_enter(_e: tk.Event) -> None:  # type: ignore[type-arg]
            card.configure(bg=hover_bg)
            inner.configure(bg=hover_bg)
            title_label.configure(bg=hover_bg)
            desc_label.configure(bg=hover_bg)

        def _on_leave(_e: tk.Event) -> None:  # type: ignore[type-arg]
            card.configure(bg=normal_bg)
            inner.configure(bg=normal_bg)
            title_label.configure(bg=normal_bg)
            desc_label.configure(bg=normal_bg)

        for widget in (shadow, card, inner, title_label, desc_label, color_bar):
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)
            widget.bind("<Button-1>", lambda _e, cb=callback: cb())

    # ------------------------------------------------------------------
    # System status + alerts (right panel) — card style
    # ------------------------------------------------------------------

    def _build_system_panel(self, row: int) -> None:
        _panel_bg = get_color("panel_bg")

        # Shadow wrapper for system panel
        shadow = tk.Frame(self, bg=get_color("shadow"))
        shadow.grid(row=row, column=1, rowspan=3, sticky="nsew",
                    padx=(0, get_spacing("md")),
                    pady=get_spacing("sm"))

        panel = tk.Frame(shadow, bg=_panel_bg)
        panel.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 3))

        # System status header with dark accent
        status_header = tk.Frame(panel, bg="#061b31", height=36)
        status_header.pack(fill="x")
        status_header.pack_propagate(False)
        tk.Label(
            status_header,
            text=t("dashboard.system_status"),
            font=get_font("header"),
            fg="#ffffff",
            bg="#061b31",
            anchor="w",
        ).pack(fill="x", padx=get_spacing("md"), pady=get_spacing("xs"))

        # DB status with 12px dot
        self._db_status_frame = tk.Frame(panel, bg=_panel_bg)
        self._db_status_frame.pack(fill="x", padx=get_spacing("md"),
                                    pady=(get_spacing("sm"), get_spacing("xs")))
        self._db_dot = tk.Canvas(
            self._db_status_frame, width=12, height=12,
            bg=_panel_bg, highlightthickness=0,
        )
        self._db_dot.pack(side="left", padx=(0, get_spacing("xs")))
        self._db_status_label = tk.Label(
            self._db_status_frame,
            text="",
            font=get_font("body"),
            fg=get_color("text"),
            bg=_panel_bg,
            anchor="w",
        )
        self._db_status_label.pack(side="left", fill="x")

        # Master key status with 12px dot
        self._key_status_frame = tk.Frame(panel, bg=_panel_bg)
        self._key_status_frame.pack(fill="x", padx=get_spacing("md"),
                                     pady=(0, get_spacing("md")))
        self._key_dot = tk.Canvas(
            self._key_status_frame, width=12, height=12,
            bg=_panel_bg, highlightthickness=0,
        )
        self._key_dot.pack(side="left", padx=(0, get_spacing("xs")))
        self._key_status_label = tk.Label(
            self._key_status_frame,
            text="",
            font=get_font("body"),
            fg=get_color("text"),
            bg=_panel_bg,
            anchor="w",
        )
        self._key_status_label.pack(side="left", fill="x")

        # Alerts header with accent
        alerts_header = tk.Frame(panel, bg="#3d1a54", height=36)
        alerts_header.pack(fill="x")
        alerts_header.pack_propagate(False)
        tk.Label(
            alerts_header,
            text=t("dashboard.alerts"),
            font=get_font("header"),
            fg="#ffffff",
            bg="#3d1a54",
            anchor="w",
        ).pack(fill="x", padx=get_spacing("md"), pady=get_spacing("xs"))

        self._alert_frame = tk.Frame(panel, bg=_panel_bg)
        self._alert_frame.pack(fill="both", expand=True,
                               padx=get_spacing("md"), pady=(get_spacing("xs"), get_spacing("md")))

    # ------------------------------------------------------------------
    # Recent history (bottom) — card style with row separators
    # ------------------------------------------------------------------

    def _build_recent_history(self, row: int) -> None:
        # Shadow wrapper
        shadow = tk.Frame(self, bg=get_color("shadow"))
        shadow.grid(row=row, column=0, sticky="nsew",
                    padx=get_spacing("md"), pady=get_spacing("sm"))

        history_frame = tk.Frame(shadow, bg=get_color("card_bg"))
        history_frame.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 3))

        # Dark header for recent activity
        hist_header = tk.Frame(history_frame, bg="#061b31", height=36)
        hist_header.pack(fill="x")
        hist_header.pack_propagate(False)
        tk.Label(
            hist_header,
            text=t("dashboard.recent_activity"),
            font=get_font("header"),
            fg="#ffffff",
            bg="#061b31",
            anchor="w",
        ).pack(fill="x", padx=get_spacing("md"), pady=get_spacing("xs"))

        # Table header row
        header_row = tk.Frame(history_frame, bg=get_color("bg"))
        header_row.pack(fill="x")
        tk.Label(
            header_row,
            text="  TIME                |  TYPE        |  SEAL ID",
            font=get_font("mono"),
            fg=get_color("text_secondary"),
            bg=get_color("bg"),
            anchor="w",
            pady=4,
        ).pack(fill="x")

        sep = tk.Frame(history_frame, height=1, bg=get_color("border"))
        sep.pack(fill="x")

        self._history_list = tk.Frame(history_frame, bg=get_color("card_bg"))
        self._history_list.pack(fill="both", expand=True, padx=0, pady=0)
        self._history_row_count = 0

    # ------------------------------------------------------------------
    # Refresh / data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload all dashboard data from the database."""
        self._refresh_stats()
        self._refresh_system_status()
        self._refresh_alerts()
        self._refresh_history()

    def _refresh_stats(self) -> None:
        from ..db.sqlite_store import get_dashboard_stats

        stats = get_dashboard_stats(self._app.db_path)
        self._stat_labels["seal_count"].configure(text=str(stats.get("sealed_only", 0)))
        self._stat_labels["unseal_count"].configure(text=str(stats.get("unsealed", 0)))
        self._stat_labels["reseal_count"].configure(text=str(stats.get("resealed", 0)))

    def _draw_status_dot(self, canvas: tk.Canvas, color: str) -> None:
        """Draw a filled 12px circle on a status dot canvas."""
        canvas.delete("all")
        canvas.create_oval(0, 0, 12, 12, fill=color, outline=color)

    def _refresh_system_status(self) -> None:
        # DB connection check
        db_ok = False
        if self._app.db_path:
            try:
                conn = sqlite3.connect(self._app.db_path)
                conn.execute("SELECT 1")
                conn.close()
                db_ok = True
            except Exception:
                pass

        if db_ok:
            self._draw_status_dot(self._db_dot, get_color("success"))
            self._db_status_label.configure(
                text=f"{t('dashboard.db_status')}: {t('dashboard.db_ok')}",
                fg=get_color("text"),
            )
        else:
            self._draw_status_dot(self._db_dot, get_color("danger"))
            self._db_status_label.configure(
                text=f"{t('dashboard.db_status')}: {t('dashboard.db_fail')}",
                fg=get_color("danger"),
            )

        # Master key check
        key_path = Path.home() / ".enc_envelope" / "master.key"
        if key_path.exists():
            self._draw_status_dot(self._key_dot, get_color("success"))
            self._key_status_label.configure(
                text=f"{t('dashboard.master_key')}: {t('dashboard.key_exists')}",
                fg=get_color("text"),
            )
        else:
            self._draw_status_dot(self._key_dot, get_color("danger"))
            self._key_status_label.configure(
                text=f"{t('dashboard.master_key')}: {t('dashboard.key_missing')}",
                fg=get_color("danger"),
            )

    def _refresh_alerts(self) -> None:
        for child in self._alert_frame.winfo_children():
            child.destroy()

        from ..db.sqlite_store import get_expiring_seals

        expiring = get_expiring_seals(self._app.db_path, days=3)

        if not expiring:
            tk.Label(
                self._alert_frame,
                text=f"\u2714 {t('dashboard.no_alerts')}",
                font=get_font("body"),
                fg=get_color("success"),
                bg=get_color("panel_bg"),
                anchor="w",
            ).pack(fill="x")
            return

        for item in expiring:
            seal_id = item.get("seal_id", "")
            unlock_str = item.get("unlock_time", "")
            # Alert with left 3px ruby border
            alert_container = tk.Frame(self._alert_frame, bg=get_color("danger"))
            alert_container.pack(fill="x", pady=2)
            alert_inner = tk.Frame(alert_container, bg=get_color("card_bg"))
            alert_inner.pack(fill="x", padx=(3, 0))  # 3px left ruby border
            tk.Label(
                alert_inner,
                text=f"\u26a0 {seal_id} \u2014 {t('dashboard.expiring_seal')}: {unlock_str}",
                font=get_font("small"),
                fg=get_color("warning"),
                bg=get_color("card_bg"),
                anchor="w",
                padx=get_spacing("xs"),
                pady=2,
            ).pack(fill="x")

    def _refresh_history(self) -> None:
        self._history_row_count = 0
        for child in self._history_list.winfo_children():
            child.destroy()

        from ..db.sqlite_store import get_recent_cases

        cases = get_recent_cases(self._app.db_path, limit=5)

        if not cases:
            tk.Label(
                self._history_list,
                text=t("dashboard.no_activity"),
                font=get_font("body"),
                fg=get_color("text_secondary"),
                bg=get_color("card_bg"),
            ).pack(pady=get_spacing("sm"))
            return

        for case in cases:
            self._add_history_row(case)

    def _add_history_row(self, case: dict) -> None:
        # Alternating row background
        row_idx = getattr(self, "_history_row_count", 0)
        self._history_row_count = row_idx + 1
        normal_bg = "#ffffff" if row_idx % 2 == 0 else "#f8f9fb"

        row_frame = tk.Frame(self._history_list, bg=normal_bg,
                             cursor="hand2")
        row_frame.pack(fill="x")

        created = case.get("created_at", "")
        status = case.get("status", "")
        seal_id = case.get("seal_id", "")

        type_text = _status_to_type(status)

        label = tk.Label(
            row_frame,
            text=f"  {created}  |  {type_text}  |  {seal_id}",
            font=get_font("mono"),
            fg=get_color("text"),
            bg=normal_bg,
            anchor="w",
            pady=5,
        )
        label.pack(fill="x")

        hover_bg = get_color("hover")

        def _on_enter(_e: tk.Event) -> None:  # type: ignore[type-arg]
            row_frame.configure(bg=hover_bg)
            label.configure(bg=hover_bg)

        def _on_leave(_e: tk.Event) -> None:  # type: ignore[type-arg]
            row_frame.configure(bg=normal_bg)
            label.configure(bg=normal_bg)

        def _on_click(_e: tk.Event) -> None:  # type: ignore[type-arg]
            self._open_case_detail(seal_id)

        for widget in (row_frame, label):
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)
            widget.bind("<Button-1>", _on_click)

    def _open_case_detail(self, seal_id: str) -> None:
        try:
            from .case_detail_dialog import CaseDetailDialog
            CaseDetailDialog(self, self._app.db_path, seal_id)
        except Exception as exc:
            logger.warning("케이스 상세 열기 실패: %s", exc)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _status_to_type(status: str) -> str:
    """Convert status code like S1U1R0 to a display type string."""
    if not status:
        return t("status.seal")
    # Check for reseal first (R >= 1)
    if "R" in status:
        try:
            r_val = int(status.split("R")[-1])
            if r_val >= 1:
                return t("status.reseal")
        except (ValueError, IndexError):
            pass
    # Check for unseal (U >= 1)
    if "U" in status:
        try:
            parts = status.split("U")
            u_part = parts[-1].split("R")[0] if "R" in parts[-1] else parts[-1]
            u_val = int(u_part)
            if u_val >= 1:
                return t("status.unseal")
        except (ValueError, IndexError):
            pass
    return t("status.seal")
