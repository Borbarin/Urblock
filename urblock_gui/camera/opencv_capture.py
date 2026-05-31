"""Захват видео с веб-камеры через OpenCV."""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from camera.frame_buffer import FrameBuffer

_SYS_VIDEO = Path("/sys/class/video4linux")


@dataclass(frozen=True)
class CameraDevice:
    index: int
    label: str


def _preferred_backend() -> int:
    if sys.platform == "linux" and hasattr(cv2, "CAP_V4L2"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def _v4l_device_name(index: int) -> str | None:
    path = _SYS_VIDEO / f"video{index}" / "name"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _is_capture_device(index: int) -> bool:
    dev = Path(f"/dev/video{index}")
    if not dev.exists():
        return False

    name = (_v4l_device_name(index) or "").lower()
    if "metadata" in name:
        return False

    index_file = _SYS_VIDEO / f"video{index}" / "index"
    if index_file.is_file():
        try:
            if "meta" in index_file.read_text(encoding="utf-8").lower():
                return False
        except OSError:
            pass

    return True


def enumerate_camera_indices() -> list[int]:
    """Список камер из sysfs — работает, даже если устройство уже занято превью."""
    if not _SYS_VIDEO.is_dir():
        return []

    indices: list[int] = []
    for entry in _SYS_VIDEO.iterdir():
        match = re.fullmatch(r"video(\d+)", entry.name)
        if not match:
            continue
        index = int(match.group(1))
        if _is_capture_device(index):
            indices.append(index)
    return sorted(indices)


def probe_camera_indices(max_index: int = 10) -> list[int]:
    """Проверка через OpenCV (устройство должно быть свободно)."""
    found: list[int] = []
    backend = _preferred_backend()
    for index in range(max_index):
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(index)
        if cap.isOpened():
            found.append(index)
        cap.release()
    return found


def list_cameras(active_index: int | None = None) -> list[CameraDevice]:
    """Все камеры для списка в настройках."""
    indices: set[int] = set(enumerate_camera_indices())
    if not indices:
        indices.update(probe_camera_indices())
    if active_index is not None:
        indices.add(active_index)

    devices: list[CameraDevice] = []
    for index in sorted(indices):
        name = _v4l_device_name(index)
        path = f"/dev/video{index}"
        in_use = active_index is not None and index == active_index
        if name:
            label = f"{name} — {path} (#{index})"
        else:
            label = f"{path} (#{index})"
        if in_use:
            label += " · в эфире"
        devices.append(CameraDevice(index=index, label=label))
    return devices


class OpenCVCamera:
    """Фоновый захват; кадры нормализуются до фиксированного размера."""

    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._target_w = 640
        self._target_h = 480
        self._frames = FrameBuffer(480, 640)
        self._scratch = np.empty((480, 640, 3), dtype=np.uint8)
        self._last_frame_bgr: np.ndarray | None = None
        self._bgr_lock = threading.Lock()
        self.device_index: int | None = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    @property
    def last_frame_bgr(self) -> np.ndarray | None:
        with self._bgr_lock:
            if self._last_frame_bgr is None:
                return None
            return self._last_frame_bgr.copy()

    def open(self, device_index: int, width: int, height: int) -> bool:
        self.close()
        self._target_w = width
        self._target_h = height
        self._frames.resize(height, width)
        self._scratch = np.empty((height, width, 3), dtype=np.uint8)

        backend = _preferred_backend()
        cap = cv2.VideoCapture(device_index, backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(device_index)

        if not cap.isOpened():
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        if hasattr(cv2, "CAP_PROP_FOURCC"):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        for _ in range(5):
            cap.grab()

        self._cap = cap
        self.device_index = device_index
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, name="opencv-camera", daemon=True)
        self._thread.start()
        return True

    def _capture_loop(self) -> None:
        tw, th = self._target_w, self._target_h
        while not self._stop.is_set() and self._cap is not None:
            if not self._cap.grab():
                time.sleep(0.02)
                continue
            ok, frame = self._cap.retrieve()
            if not ok or frame is None:
                time.sleep(0.01)
                continue

            with self._bgr_lock:
                self._last_frame_bgr = frame

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            if w != tw or h != th:
                cv2.resize(rgb, (tw, th), dst=self._scratch, interpolation=cv2.INTER_LINEAR)
                self._frames.publish(self._scratch)
            else:
                self._frames.publish(rgb)

    def take_frame(self) -> np.ndarray | None:
        return self._frames.consume_copy()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        with self._bgr_lock:
            self._last_frame_bgr = None
        self.device_index = None
