"""Progress dialog for long-running encryption/decryption operations.

Displays a progress bar, chunk counter, elapsed/remaining time,
and cancel button.  All heavy work runs on a background thread;
UI updates happen on the main thread via ``root.after()``.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .i18n import t


class ProgressDialog(tk.Toplevel):
    """Modal dialog showing progress of a background task with time tracking.

    Usage::

        def my_task(progress_cb):
            for i in range(10):
                do_work(i)
                progress_cb(i + 1, 10)

        dlg = ProgressDialog(root, title="암호화 진행", task_fn=my_task)
        root.wait_window(dlg)
        print(dlg.elapsed_seconds, dlg.start_time_iso, dlg.end_time_iso)
    """

    POLL_INTERVAL_MS = 100

    def __init__(
        self,
        master: tk.Widget,
        *,
        title: str = "",
        task_fn: Callable[[Callable[[int, int], None]], object],
        on_complete: Optional[Callable[[object], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        super().__init__(master)
        self.title(title or t("progress.title"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.grab_set()

        self._task_fn = task_fn
        self._on_complete = on_complete
        self._on_error = on_error

        self._cancelled = threading.Event()
        self._current_chunk = 0
        self._total_chunks = 0
        self._lock = threading.Lock()
        self._task_result: object = None
        self._task_done = threading.Event()
        self.result_error: Optional[Exception] = None

        # Time tracking
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self.start_time_iso: str = ""
        self.end_time_iso: str = ""
        self.elapsed_seconds: float = 0.0

        self._build_ui()
        self._center_window()
        self._start_task()

    def _build_ui(self) -> None:
        frame = tk.Frame(self, padx=20, pady=16)
        frame.pack(fill="both", expand=True)

        self._status_label = tk.Label(
            frame, text=t("progress.preparing"), anchor="w", font=("맑은 고딕", 10)
        )
        self._status_label.pack(fill="x", pady=(0, 6))

        self._progress_var = tk.DoubleVar(value=0.0)
        self._progress_bar = ttk.Progressbar(
            frame, variable=self._progress_var,
            maximum=100.0, length=450, mode="determinate",
        )
        self._progress_bar.pack(fill="x", pady=(0, 6))

        # Chunk + percentage row
        info_frame = tk.Frame(frame)
        info_frame.pack(fill="x", pady=(0, 4))
        self._chunk_label = tk.Label(info_frame, text=t("progress.chunk_label").format(current=0, total=0), anchor="w")
        self._chunk_label.pack(side="left")
        self._pct_label = tk.Label(info_frame, text="0.0%", anchor="e")
        self._pct_label.pack(side="right")

        # Time row
        time_frame = tk.Frame(frame)
        time_frame.pack(fill="x", pady=(0, 4))
        self._elapsed_label = tk.Label(
            time_frame, text=t("progress.elapsed_label").format(time="00:00:00"), anchor="w", fg="#555"
        )
        self._elapsed_label.pack(side="left")
        self._remaining_label = tk.Label(
            time_frame, text=t("progress.remaining_calc"), anchor="e", fg="#555"
        )
        self._remaining_label.pack(side="right")

        # Speed row
        self._speed_label = tk.Label(
            frame, text="", anchor="w", fg="#888", font=("맑은 고딕", 8)
        )
        self._speed_label.pack(fill="x", pady=(0, 8))

        self._cancel_btn = tk.Button(
            frame, text=t("progress.cancel"), width=12, command=self._on_cancel
        )
        self._cancel_btn.pack()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        parent = self.master
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _progress_callback(self, current: int, total: int) -> None:
        """Thread-safe progress update called from the background task."""
        if self._cancelled.is_set():
            raise _CancelledError(t("progress.user_cancelled"))
        with self._lock:
            self._current_chunk = current
            self._total_chunks = total

    def _start_task(self) -> None:
        from datetime import datetime, timezone
        self._start_time = time.monotonic()
        self.start_time_iso = datetime.now(timezone.utc).astimezone().isoformat()
        thread = threading.Thread(target=self._run_task, daemon=True)
        thread.start()
        self._poll_progress()

    def _run_task(self) -> None:
        try:
            result = self._task_fn(self._progress_callback)
            self._task_result = result
        except _CancelledError:
            self.result_error = _CancelledError(t("progress.task_cancelled"))
        except Exception as exc:
            self.result_error = exc
        finally:
            self._end_time = time.monotonic()
            from datetime import datetime, timezone
            self.end_time_iso = datetime.now(timezone.utc).astimezone().isoformat()
            self.elapsed_seconds = self._end_time - self._start_time
            self._task_done.set()

    def _poll_progress(self) -> None:
        if self._task_done.is_set():
            self._finish()
            return

        with self._lock:
            current = self._current_chunk
            total = self._total_chunks

        elapsed = time.monotonic() - self._start_time

        if total > 0 and current > 0:
            pct = (current / total) * 100.0
            self._progress_var.set(pct)
            self._chunk_label.configure(text=t("progress.chunk_label").format(current=current, total=total))
            self._pct_label.configure(text=f"{pct:.1f}%")
            self._status_label.configure(text=t("progress.processing").format(current=current, total=total))

            # Estimated remaining time
            rate = elapsed / current  # seconds per chunk
            remaining = rate * (total - current)
            self._elapsed_label.configure(text=t("progress.elapsed_label").format(time=_fmt_time(elapsed)))
            self._remaining_label.configure(text=t("progress.remaining_label").format(time=_fmt_time(remaining)))

            # Speed
            if elapsed > 0:
                chunks_per_sec = current / elapsed
                self._speed_label.configure(
                    text=t("progress.speed_label").format(rate=chunks_per_sec)
                )
        else:
            self._elapsed_label.configure(text=t("progress.elapsed_label").format(time=_fmt_time(elapsed)))
            self._status_label.configure(text=t("progress.preparing"))

        self.after(self.POLL_INTERVAL_MS, self._poll_progress)

    def _finish(self) -> None:
        elapsed = self.elapsed_seconds
        if self.result_error is not None:
            self._status_label.configure(
                text=t("progress.error").format(time=_fmt_time(elapsed))
            )
            if self._on_error is not None:
                self._on_error(self.result_error)
        else:
            self._progress_var.set(100.0)
            self._pct_label.configure(text="100.0%")
            self._status_label.configure(
                text=t("progress.complete").format(time=_fmt_time(elapsed))
            )
            self._remaining_label.configure(text=t("progress.remaining_zero"))
            if self._on_complete is not None:
                self._on_complete(self._task_result)
        self.grab_release()
        self.destroy()

    def _on_cancel(self) -> None:
        self._cancelled.set()
        self._cancel_btn.configure(state="disabled")
        self._status_label.configure(text=t("progress.cancelling"))

    @property
    def was_cancelled(self) -> bool:
        return self._cancelled.is_set()


def _fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS using i18n."""
    s = int(seconds)
    if s < 0:
        return t("time.zero")
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return t("time.fmt_hms").format(h=h, m=m, s=sec)
    if m > 0:
        return t("time.fmt_ms").format(m=m, s=sec)
    return t("time.fmt_s").format(s=sec)


class _CancelledError(Exception):
    """Internal exception to signal task cancellation."""
