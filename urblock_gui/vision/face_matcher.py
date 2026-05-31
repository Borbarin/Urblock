"""Сравнение лица в кадре с сохранённой галереей (SFace)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config import (
    EMBEDDING_SELF_CHECK_MIN,
    FACE_MODEL_PATH,
    MATCH_THRESHOLD_DEFAULT,
    SFACE_MODEL_PATH,
)
from storage import (
    iter_gallery_images,
    load_biometric_image_bgr,
    load_embedding,
    load_face_snapshot_bgr,
    save_embedding,
)
from vision.face_detector import FaceDetection, YuNetDetector


@dataclass
class GalleryEntry:
    entry_id: str
    name: str
    source: str
    feature: np.ndarray


@dataclass
class MatchResult:
    name: str
    score: float
    entry_id: str
    is_match: bool


class FaceMatcher:
    def __init__(self) -> None:
        self._detector = YuNetDetector()
        self._recognizer: cv2.FaceRecognizerSF | None = None
        self._gallery: list[GalleryEntry] = []
        self._gallery_dirty = True

    @property
    def is_ready(self) -> bool:
        return FACE_MODEL_PATH.is_file() and SFACE_MODEL_PATH.is_file()

    @property
    def gallery_size(self) -> int:
        return len(self._gallery)

    def mark_gallery_dirty(self) -> None:
        self._gallery_dirty = True

    def _ensure_recognizer(self) -> bool:
        if self._recognizer is not None:
            return True
        if not SFACE_MODEL_PATH.is_file():
            return False
        self._recognizer = cv2.FaceRecognizerSF.create(str(SFACE_MODEL_PATH), "")
        return True

    def ensure_gallery(self) -> int:
        """Перезагружает галерею только если были изменения (не каждый кадр)."""
        if not self._gallery_dirty:
            return len(self._gallery)
        return self.reload_gallery()

    def reload_gallery(self) -> int:
        self._gallery.clear()
        self._gallery_dirty = False
        if not self._ensure_recognizer():
            return 0

        for item in iter_gallery_images():
            try:
                feature = self._resolve_gallery_feature(item)
                if feature is not None:
                    self._gallery.append(
                        GalleryEntry(
                            entry_id=item.entry_id,
                            name=item.name,
                            source=item.source,
                            feature=feature,
                        )
                    )
            except Exception:
                continue
        return len(self._gallery)

    def _feature_from_detection(
        self, frame_bgr: np.ndarray, face: FaceDetection
    ) -> np.ndarray | None:
        if not self._ensure_recognizer():
            return None
        aligned = self._recognizer.alignCrop(frame_bgr, face.to_yunet_row())
        return self._recognizer.feature(aligned)

    def _feature_from_bgr(self, image_bgr: np.ndarray) -> np.ndarray | None:
        faces = self._detector.detect(image_bgr)
        face = YuNetDetector.largest(faces)
        if face is None:
            return None
        return self._feature_from_detection(image_bgr, face)

    def _feature_from_image(self, image_path: Path) -> np.ndarray | None:
        image = cv2.imread(str(image_path))
        if image is None:
            return None
        return self._feature_from_bgr(image)

    def _embedding_consistent(self, stored: np.ndarray, fresh: np.ndarray) -> bool:
        if not self._ensure_recognizer():
            return False
        score = float(
            self._recognizer.match(stored, fresh, cv2.FaceRecognizerSF_FR_COSINE)
        )
        return score >= EMBEDDING_SELF_CHECK_MIN

    def _resolve_gallery_feature(self, item) -> np.ndarray | None:
        """Берёт зашифрованный эмбеддинг или пересчитывает из снимка/vault."""
        feature = load_embedding(item.entry_id, source=item.source)
        reference_bgr = None
        if item.source == "biometric":
            reference_bgr = load_biometric_image_bgr(item.entry_id)
        elif item.source == "face":
            reference_bgr = load_face_snapshot_bgr(item.entry_id)

        if reference_bgr is not None:
            fresh = self._feature_from_bgr(reference_bgr)
            if fresh is None:
                return None
            if feature is not None and self._embedding_consistent(feature, fresh):
                return feature
            save_embedding(item.entry_id, fresh, source=item.source)
            return fresh

        if feature is not None:
            return feature
        return None

    def register_detection(
        self, entry_id: str, frame_bgr: np.ndarray, face: FaceDetection, source: str = "face"
    ) -> bool:
        feature = self._feature_from_detection(frame_bgr, face)
        if feature is None:
            return False
        save_embedding(entry_id, feature, source=source)
        self.mark_gallery_dirty()
        self.ensure_gallery()
        return True

    def register_image(self, entry_id: str, image_path: Path, source: str = "face") -> bool:
        feature = self._feature_from_image(image_path)
        if feature is None:
            return False
        save_embedding(entry_id, feature, source=source)
        self.mark_gallery_dirty()
        self.ensure_gallery()
        return True

    def register_frame_image(
        self,
        entry_id: str,
        image_bgr: np.ndarray,
        source: str = "face",
        *,
        reload: bool = True,
    ) -> bool:
        feature = self._feature_from_bgr(image_bgr)
        if feature is None:
            return False
        save_embedding(entry_id, feature, source=source)
        self.mark_gallery_dirty()
        if reload:
            self.ensure_gallery()
        return True

    def match_frame(
        self,
        frame_bgr: np.ndarray,
        threshold: float = MATCH_THRESHOLD_DEFAULT,
        *,
        detect_max_width: int | None = None,
    ) -> tuple[list[FaceDetection], MatchResult | None]:
        self.ensure_gallery()

        if detect_max_width is not None and frame_bgr.shape[1] > detect_max_width:
            scale = detect_max_width / frame_bgr.shape[1]
            frame_bgr = cv2.resize(
                frame_bgr,
                (detect_max_width, max(1, int(frame_bgr.shape[0] * scale))),
                interpolation=cv2.INTER_LINEAR,
            )

        faces = self._detector.detect(frame_bgr)
        if not faces:
            return [], None

        if not self._gallery or not self._ensure_recognizer():
            return faces, None

        face = YuNetDetector.largest(faces)
        if face is None:
            return faces, None

        query = self._feature_from_detection(frame_bgr, face)
        if query is None:
            return faces, None

        best_score = -1.0
        best_entry: GalleryEntry | None = None
        for entry in self._gallery:
            score = self._recognizer.match(
                query, entry.feature, cv2.FaceRecognizerSF_FR_COSINE
            )
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is None:
            return faces, None

        return faces, MatchResult(
            name=best_entry.name,
            score=float(best_score),
            entry_id=best_entry.entry_id,
            is_match=float(best_score) >= threshold,
        )
