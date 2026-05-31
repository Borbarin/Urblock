"""Захват камеры с гарантированным освобождением устройства."""

from __future__ import annotations

from typing import Any

_active: "CameraSession | None" = None


class CameraSession:
    def __init__(self, log) -> None:
        self._log = log
        self._cap: Any = None
        self.index = -1

    def open(self, cv2, preferred_index: int, width: int, height: int) -> bool:
        global _active
        candidates: list[int] = []
        for idx in (preferred_index, 0, 1):
            if idx not in candidates:
                candidates.append(idx)

        for idx in candidates:
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if ok and frame is not None:
                self._cap = cap
                self.index = idx
                _active = self
                self._log(f"camera opened: index={idx} {frame.shape[1]}x{frame.shape[0]}")
                return True
            cap.release()
        return False

    def read(self):
        if self._cap is None:
            return False, None
        return self._cap.read()

    def close(self) -> None:
        global _active
        if self._cap is None:
            return
        try:
            if self._cap.isOpened():
                self._cap.release()
        except Exception as exc:
            self._log(f"camera release warning: {exc!r}")
        self._cap = None
        if _active is self:
            _active = None
        self._log("camera released")


def close_active(log) -> None:
    global _active
    if _active is not None:
        _active.close()
