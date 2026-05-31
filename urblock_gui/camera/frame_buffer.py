"""Потокобезопасный двойной буфер кадров (без «рваных» смен)."""

from __future__ import annotations

import threading

import numpy as np


class FrameBuffer:
    def __init__(self, height: int, width: int) -> None:
        self._height = height
        self._width = width
        self._lock = threading.Lock()
        self._buffers = [
            np.empty((height, width, 3), dtype=np.uint8),
            np.empty((height, width, 3), dtype=np.uint8),
        ]
        self._write_idx = 0
        self._read_idx = -1
        self._sequence = 0
        self._consumed_sequence = -1

    def resize(self, height: int, width: int) -> None:
        with self._lock:
            self._height = height
            self._width = width
            self._buffers = [
                np.empty((height, width, 3), dtype=np.uint8),
                np.empty((height, width, 3), dtype=np.uint8),
            ]
            self._write_idx = 0
            self._read_idx = -1
            self._sequence = 0
            self._consumed_sequence = -1

    def publish(self, rgb: np.ndarray) -> None:
        """Публикует готовый кадр H×W×3 uint8."""
        with self._lock:
            np.copyto(self._buffers[self._write_idx], rgb)
            self._read_idx = self._write_idx
            self._write_idx = 1 - self._write_idx
            self._sequence += 1

    def consume_copy(self) -> np.ndarray | None:
        """Копия последнего кадра, если с прошлого вызова был новый."""
        with self._lock:
            if self._read_idx < 0 or self._sequence == self._consumed_sequence:
                return None
            self._consumed_sequence = self._sequence
            return self._buffers[self._read_idx].copy()
