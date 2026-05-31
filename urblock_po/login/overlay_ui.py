#!/usr/bin/env python3
"""Окно-индикатор биометрии на экране входа / блокировки (tkinter)."""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

FONT_FAMILIES = ("DejaVu Sans", "Ubuntu", "Liberation Sans", "Sans", "sans-serif")


class OverlayWindow:
    BG = "#1e2430"
    FG = "#e8ecf4"
    FG_DIM = "#aab4c4"
    ACCENT = "#5cdb95"
    WARN = "#f4a261"
    OK = "#4ade80"
    BORDER = "#3d4f6a"

    TEXT = {
        "starting": ("Urblock", "Подготовка камеры…", False),
        "camera_ok": ("Urblock", "Камера включена\nвведите пароль или смотрите в камеру", True),
        "scanning": ("Urblock", "Ищем лицо…\nможно вводить пароль", True),
        "no_face": ("Urblock", "Лицо не видно\nповернитесь к камере", True),
        "no_match": ("Urblock", "Лицо не распознано\nвведите пароль", True),
        "success": ("Готово", "Лицо распознано — вход…", True),
        "failed": ("Urblock", "Не удалось войти\nпроверьте пароль", False),
        "stop": ("", "", False),
    }

    GREETER_TEXT = {
        "starting": ("Urblock", "Включаем камеру…", False),
        "camera_ok": ("Идёт распознавание", "Смотрите в камеру\nможно ввести пароль ниже", True),
        "scanning": ("Идёт распознавание", "Смотрите в камеру\nможно ввести пароль ниже", True),
        "no_face": ("Urblock", "Повернитесь к камере", True),
        "no_match": ("Urblock", "Лицо не распознано\nвведите пароль", True),
        "success": ("Готово", "Лицо распознано — нажмите Enter", True),
        "failed": ("Urblock", "Не удалось войти", False),
        "stop": ("", "", False),
    }

    def __init__(
        self,
        status_queue: queue.Queue[str],
        status_file: str | None = None,
        *,
        greeter: bool = False,
    ) -> None:
        self._greeter = greeter
        self._text_map = self.GREETER_TEXT if greeter else self.TEXT
        self._queue = status_queue
        self._status_file = Path(status_file) if status_file else None
        self._last_file_status = ""
        self._pulse_on = False
        self._pulse_state = False

        self.root = tk.Tk()
        self.root.title("Urblock")
        self.root.configure(bg=self.BG)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        w, h = (420, 160) if greeter else (380, 140)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, sw - w - 28)
        y = 48 if not greeter else max(48, sh // 2 - h // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(w, h)

        outer = tk.Frame(self.root, bg=self.BORDER, padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)
        frame = tk.Frame(outer, bg=self.BG, padx=18, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        row = tk.Frame(frame, bg=self.BG)
        row.pack(fill=tk.X)

        self._canvas = tk.Canvas(row, width=36, height=36, bg=self.BG, highlightthickness=0)
        self._canvas.pack(side=tk.LEFT, padx=(0, 14))
        self._dot = self._canvas.create_oval(8, 8, 28, 28, fill="#555", outline="")

        text_col = tk.Frame(row, bg=self.BG)
        text_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._title_font = tkfont.Font(family=FONT_FAMILIES, size=13, weight="bold")
        self._body_font = tkfont.Font(family=FONT_FAMILIES, size=11)

        self._title = tk.Label(
            text_col,
            text="Urblock",
            bg=self.BG,
            fg=self.FG,
            font=self._title_font,
            anchor="w",
        )
        self._title.pack(fill=tk.X)
        self._body = tk.Label(
            text_col,
            text="Проверка лица…",
            bg=self.BG,
            fg=self.FG_DIM,
            font=self._body_font,
            anchor="w",
            wraplength=300,
            justify=tk.LEFT,
        )
        self._body.pack(fill=tk.X, pady=(4, 0))

        self._draw_camera_icon(False)
        self._apply("starting")
        self.root.update_idletasks()
        self.root.lift()
        self.root.attributes("-topmost", True)

        self.root.after(80, self._poll_queue)
        if self._status_file:
            self.root.after(200, self._poll_status_file)
        self.root.after(400, self._pulse)

    def _draw_camera_icon(self, active: bool) -> None:
        self._canvas.delete("cam")
        color = self.ACCENT if active else "#6b7280"
        self._canvas.create_rectangle(6, 12, 30, 26, outline=color, width=2, tags="cam")
        self._canvas.create_polygon(30, 14, 34, 16, 34, 22, 30, 24, outline=color, fill="", width=2, tags="cam")
        self._canvas.create_oval(14, 17, 22, 23, outline=color, width=2, tags="cam")

    def _pulse(self) -> None:
        if not self._pulse_on:
            self._canvas.itemconfig(self._dot, fill="#555")
        else:
            self._pulse_state = not self._pulse_state
            self._canvas.itemconfig(self._dot, fill=self.ACCENT if self._pulse_state else "#2d6a4f")
        self.root.after(450, self._pulse)

    def _poll_queue(self) -> None:
        try:
            while True:
                status = self._queue.get_nowait()
                if status:
                    self._apply(status)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _apply(self, status: str) -> None:
        if status == "stop":
            self.root.after(100, self.root.destroy)
            return
        if status.startswith("match_progress:"):
            try:
                percent = float(status.split(":", 1)[1])
            except ValueError:
                percent = 0.0
            title = "Идёт распознавание" if self._greeter else "Urblock"
            self._title.config(text=title, fg=self.FG)
            self._body.config(
                text=f"Смотрите в камеру… {percent:.0f}%",
                fg=self.FG_DIM,
            )
            self._pulse_on = True
            self._draw_camera_icon(True)
            self.root.update_idletasks()
            return
        title, body, pulse = self._text_map.get(status, self._text_map["scanning"])
        if title:
            self._title.config(text=title, fg=self.FG)
        self._body.config(text=body, fg=self.FG_DIM)
        active = status in ("camera_ok", "scanning", "success", "no_face", "no_match")
        self._pulse_on = pulse
        self._draw_camera_icon(active)
        if status == "success":
            self._canvas.itemconfig(self._dot, fill=self.OK)
            self._title.config(fg=self.OK)
        elif status in ("failed", "no_match"):
            self._canvas.itemconfig(self._dot, fill=self.WARN)
        elif status == "no_face":
            self._canvas.itemconfig(self._dot, fill="#888")
        self.root.update_idletasks()

    def _poll_status_file(self) -> None:
        if not self._status_file:
            return
        try:
            if self._status_file.is_file():
                status = self._status_file.read_text(encoding="utf-8").strip().split("\n", 1)[0]
                if status and status != self._last_file_status:
                    self._last_file_status = status
                    self._apply(status)
        except OSError:
            pass
        self.root.after(200, self._poll_status_file)

    def run(self) -> None:
        self.root.mainloop()


def _pipe_reader(pipe_path: str, status_queue: queue.Queue[str]) -> None:
    try:
        with open(pipe_path, encoding="utf-8") as fifo:
            for line in fifo:
                status = line.strip()
                if status:
                    status_queue.put(status)
    except OSError as exc:
        sys.stderr.write(f"urblock overlay pipe: {exc}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipe", required=True, help="FIFO path for status updates")
    parser.add_argument("--status-file", help="Fallback status file (polled if pipe stalls)")
    parser.add_argument(
        "--greeter",
        action="store_true",
        help="Экран входа GDM: крупнее и заметнее",
    )
    args = parser.parse_args()
    if not os.path.exists(args.pipe):
        sys.stderr.write(f"urblock overlay: missing pipe {args.pipe}\n")
        return 1

    status_queue: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=_pipe_reader,
        args=(args.pipe, status_queue),
        name="urblock-overlay-pipe",
        daemon=True,
    ).start()

    try:
        OverlayWindow(status_queue, args.status_file, greeter=args.greeter).run()
    except tk.TclError as exc:
        sys.stderr.write(f"urblock overlay tk: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
