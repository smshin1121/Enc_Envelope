"""Horizontal step indicator widget for wizard UX.

Renders a sequence of numbered circles connected by lines.
Each step has three visual states: completed, active, and pending.
Styled per DESIGN.md Step Indicator specifications.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from .theme import COLORS, FONTS, get_color


class StepIndicator(tk.Canvas):
    """Horizontal step indicator showing circle + title + connecting lines."""

    CIRCLE_RADIUS = 16  # 32px diameter — more prominent
    LINE_HEIGHT = 3

    STATE_COMPLETED = "completed"  # Filled purple circle + white checkmark
    STATE_ACTIVE = "active"        # Filled purple circle + white number
    STATE_PENDING = "pending"      # Grey border + grey number

    def __init__(
        self,
        master: tk.Widget,
        steps: list[str],
        on_step_click: Optional[callable] = None,
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("height", 68)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bg", get_color("card_bg"))
        super().__init__(master, **kwargs)

        self._steps = list(steps)
        self._active_index = 0
        self._on_step_click = on_step_click
        self._positions: list[int] = []

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_canvas_click)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_active(self, index: int) -> None:
        """Set the given step as active.

        Steps before *index* become completed; steps after become pending.
        """
        if index < 0 or index >= len(self._steps):
            return
        self._active_index = index
        self._draw()

    # ------------------------------------------------------------------
    # Internal drawing
    # ------------------------------------------------------------------

    def _on_configure(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._draw()

    def _draw(self) -> None:
        """Redraw the entire indicator."""
        self.delete("all")

        width = self.winfo_width()
        if width <= 1:
            return

        n = len(self._steps)
        if n == 0:
            return

        r = self.CIRCLE_RADIUS
        # Vertical center for circles -- leave room for label below
        cy = r + 4
        # Horizontal padding
        pad = max(r + 8, 30)
        usable = width - 2 * pad

        if n == 1:
            positions = [width // 2]
        else:
            spacing = usable / (n - 1)
            positions = [int(pad + i * spacing) for i in range(n)]

        self._positions = positions

        # Colors per DESIGN.md
        color_primary = get_color("primary")       # #533afd
        color_pending_border = get_color("pending_border")
        color_pending_text = get_color("text_secondary")  # #64748d
        color_line_done = get_color("primary")
        color_line_pending = get_color("pending_border")
        color_heading = get_color("heading")       # #061b31

        # Draw connecting lines first (behind circles)
        for i in range(n - 1):
            x1 = positions[i] + r
            x2 = positions[i + 1] - r
            line_y = cy

            if i < self._active_index:
                line_color = color_line_done
            else:
                line_color = color_line_pending

            self.create_line(
                x1, line_y, x2, line_y,
                fill=line_color,
                width=self.LINE_HEIGHT,
            )

        # Draw circles and labels — bold active/completed, normal pending
        label_font_normal = (FONTS["body"][0], FONTS["body"][1])
        label_font_bold = (FONTS["body"][0], FONTS["body"][1], "bold")
        num_font = (FONTS["body"][0], 11, "bold")
        check_font = (FONTS["body"][0], 13, "bold")

        for i, (cx, step_name) in enumerate(zip(positions, self._steps)):
            state = self._get_state(i)

            if state == self.STATE_COMPLETED:
                # Filled purple circle
                self.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=color_primary,
                    outline=color_primary,
                    width=2,
                )
                # White checkmark — larger
                self.create_text(
                    cx, cy,
                    text="\u2713",
                    fill="white",
                    font=check_font,
                )
            elif state == self.STATE_ACTIVE:
                # Outer glow ring
                glow_r = r + 3
                self.create_oval(
                    cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                    fill="",
                    outline="#b9b9f9",
                    width=2,
                )
                # Filled purple circle + white number
                self.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=color_primary,
                    outline=color_primary,
                    width=2,
                )
                # White number bold
                self.create_text(
                    cx, cy,
                    text=str(i + 1),
                    fill="white",
                    font=num_font,
                )
            else:
                # Pending: border only + grey number
                self.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill="white",
                    outline=color_pending_border,
                    width=2,
                )
                # Grey number
                self.create_text(
                    cx, cy,
                    text=str(i + 1),
                    fill=color_pending_text,
                    font=num_font,
                )

            # Step label below circle — bold for active
            if state == self.STATE_ACTIVE:
                label_color = color_heading
                label_font = label_font_bold
            elif state == self.STATE_COMPLETED:
                label_color = color_heading
                label_font = label_font_normal
            else:
                label_color = color_pending_text
                label_font = label_font_normal

            self.create_text(
                cx, cy + r + 12,
                text=step_name,
                fill=label_color,
                font=label_font,
                anchor="n",
            )

    def _get_state(self, index: int) -> str:
        """Return the visual state for the step at *index*."""
        if index < self._active_index:
            return self.STATE_COMPLETED
        if index == self._active_index:
            return self.STATE_ACTIVE
        return self.STATE_PENDING

    def _on_canvas_click(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Find the closest step to the click position and invoke callback."""
        if not self._on_step_click or not self._positions:
            return

        # Find closest step by x-coordinate
        min_dist = float("inf")
        closest_idx = 0
        for i, cx in enumerate(self._positions):
            dist = abs(event.x - cx)
            if dist < min_dist:
                min_dist = dist
                closest_idx = i

        self._on_step_click(closest_idx)
