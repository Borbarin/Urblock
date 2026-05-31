from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from config import FACE_MODEL_PATH


@dataclass
class FaceDetection:
    x: int
    y: int
    w: int
    h: int
    score: float
    landmarks: np.ndarray  # (5, 2)

    def to_yunet_row(self) -> np.ndarray:
        row = np.zeros((1, 15), dtype=np.float32)
        row[0, 0:4] = (self.x, self.y, self.w, self.h)
        row[0, 4:14] = self.landmarks.reshape(1, 10)
        row[0, 14] = self.score
        return row


class YuNetDetector:
    def __init__(self) -> None:
        self._detector: cv2.FaceDetectorYN | None = None
        self._input_size: tuple[int, int] | None = None

    def _ensure_loaded(self) -> bool:
        if self._detector is not None:
            return True
        if not FACE_MODEL_PATH.is_file():
            return False
        self._detector = cv2.FaceDetectorYN.create(
            str(FACE_MODEL_PATH),
            "",
            (320, 320),
            score_threshold=0.6,
            nms_threshold=0.3,
            top_k=5000,
        )
        return True

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        if not self._ensure_loaded():
            return []

        h, w = frame_bgr.shape[:2]
        if self._input_size != (w, h):
            self._detector.setInputSize((w, h))
            self._input_size = (w, h)

        _, faces = self._detector.detect(frame_bgr)
        if faces is None:
            return []

        if faces.ndim == 3:
            faces = faces[0]

        results: list[FaceDetection] = []
        for row in faces:
            x, y, bw, bh = row[:4].astype(int)
            landmarks = row[4:14].reshape(5, 2)
            score = float(row[14])
            results.append(
                FaceDetection(
                    x=max(0, x),
                    y=max(0, y),
                    w=max(1, bw),
                    h=max(1, bh),
                    score=score,
                    landmarks=landmarks,
                )
            )
        return results

    @staticmethod
    def largest(faces: list[FaceDetection]) -> FaceDetection | None:
        if not faces:
            return None
        return max(faces, key=lambda f: f.w * f.h)
