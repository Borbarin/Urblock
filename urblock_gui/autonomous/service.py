"""Фоновая проверка лица и разблокировка профиля."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autonomous.knowledge import AutonomousKnowledge
from camera.opencv_capture import OpenCVCamera
from config import FRAME_HEIGHT, FRAME_WIDTH

if TYPE_CHECKING:
    from main import UrblockApp

# Реже, чем превью — отдельная камера и тяжёлая модель
AUTONOMOUS_EVERY_N_FRAMES = 8


class AutonomousUnlockService:
    def __init__(self, app: UrblockApp) -> None:
        self._app = app
        self._detect_camera = OpenCVCamera()
        self._last_gallery_count = -1
        self._tick_id = 0

    @property
    def _matcher(self):
        return self._app.camera._faces.matcher

    def release(self) -> None:
        self._detect_camera.close()

    def _knowledge(self) -> AutonomousKnowledge:
        return AutonomousKnowledge.from_settings(self._app.settings)

    def _on_gallery_count(self, count: int) -> None:
        if count != self._last_gallery_count:
            self._last_gallery_count = count
            if count == 0:
                self._app.profile.lock()

    def _frame_bgr(self, knowledge: AutonomousKnowledge):
        import cv2

        preview = self._app.camera._capture
        if knowledge.detect_camera_index == knowledge.preview_camera_index:
            if preview.is_open:
                return preview.last_frame_bgr
            return None

        if not self._detect_camera.is_open:
            if not self._detect_camera.open(
                knowledge.detect_camera_index, FRAME_WIDTH, FRAME_HEIGHT
            ):
                return None
        frame_rgb = self._detect_camera.take_frame()
        if frame_rgb is None:
            return self._detect_camera.last_frame_bgr
        return cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    def tick(self) -> None:
        self._tick_id += 1

        if self._tick_id % 30 == 1:
            gallery_count = self._matcher.ensure_gallery()
        else:
            gallery_count = self._matcher.gallery_size
        self._on_gallery_count(gallery_count)

        knowledge = self._knowledge()
        if not knowledge.auto_detect_enabled:
            return
        if not knowledge.models_ready:
            return
        if gallery_count == 0:
            return
        if self._tick_id % AUTONOMOUS_EVERY_N_FRAMES != 0:
            return

        frame_bgr = self._frame_bgr(knowledge)
        if frame_bgr is None:
            return

        _, match = self._matcher.match_frame(frame_bgr, threshold=knowledge.match_threshold)
        if match is None or not match.is_match:
            return

        if self._app.profile.locked or self._app.profile.owner_entry_id != match.entry_id:
            self._app.profile.unlock(match.name, match.entry_id, match.score)
