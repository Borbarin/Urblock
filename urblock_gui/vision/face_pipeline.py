"""Детекция, сравнение и отрисовка рамок на превью."""

from __future__ import annotations

import cv2
import numpy as np

from config import MATCH_THRESHOLD_DEFAULT
from vision.face_detector import FaceDetection
from vision.face_matcher import FaceMatcher, MatchResult

# Полный цикл YuNet+SFace тяжёлый — не чаще чем раз в N кадров
PROCESS_EVERY_N_FRAMES = 3


class FacePipeline:
    def __init__(self) -> None:
        self._matcher = FaceMatcher()
        self._matcher.ensure_gallery()
        self._frame_id = 0
        self._last_match: MatchResult | None = None
        self._last_faces: list[FaceDetection] = []

    @property
    def is_ready(self) -> bool:
        return self._matcher.is_ready

    @property
    def matcher(self) -> FaceMatcher:
        return self._matcher

    def reload_gallery(self) -> int:
        self._matcher.mark_gallery_dirty()
        return self._matcher.ensure_gallery()

    def process(
        self, frame_rgb: np.ndarray, settings: dict
    ) -> tuple[np.ndarray, MatchResult | None]:
        if not self.is_ready:
            return frame_rgb, None

        self._frame_id += 1
        run_heavy = self._frame_id % PROCESS_EVERY_N_FRAMES == 0

        if run_heavy:
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            threshold = float(settings.get("match_threshold", MATCH_THRESHOLD_DEFAULT))
            faces, match = self._matcher.match_frame(frame_bgr, threshold=threshold)
            self._last_faces = faces
            self._last_match = match
        else:
            faces = self._last_faces
            match = self._last_match

        if not faces:
            return frame_rgb, match

        output = frame_rgb.copy()
        color = settings.get("face_box_color", [0, 255, 0])
        box_rgb = (int(color[0]), int(color[1]), int(color[2]))
        h, w = output.shape[:2]
        primary = max(faces, key=lambda f: f.w * f.h)

        for face in faces:
            is_primary = face is primary
            x1 = max(0, face.x)
            y1 = max(0, face.y)
            x2 = min(w, face.x + face.w)
            y2 = min(h, face.y + face.h)

            if is_primary and match and match.is_match:
                draw_color = (0, 220, 80)
            elif is_primary and match:
                draw_color = (220, 80, 80)
            else:
                draw_color = box_rgb

            cv2.rectangle(output, (x1, y1), (x2, y2), draw_color, 2, lineType=cv2.LINE_AA)

            if is_primary and match:
                self._draw_label(output, match, x1, max(0, y1 - 8))

        return output, match

    @staticmethod
    def _draw_label(frame: np.ndarray, match: MatchResult, x: int, y: int) -> None:
        if match.is_match:
            text = f"{match.name} {match.score * 100:.0f}%"
        else:
            text = f"? {match.score * 100:.0f}%"
        cv2.putText(
            frame,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            lineType=cv2.LINE_AA,
        )
