"""Справочник: что знает автономная подсистема разблокировки профиля."""

from __future__ import annotations

from dataclasses import dataclass

from auth.user import get_current_user_id
from config import FACE_MODEL_PATH, MATCH_THRESHOLD_DEFAULT, SFACE_MODEL_PATH


@dataclass(frozen=True)
class AutonomousKnowledge:
    """Минимальный набор данных для одного цикла автономной проверки."""

    user_id: str
    match_threshold: float
    preview_camera_index: int
    detect_camera_index: int
    auto_detect_enabled: bool
    gallery_required: bool = True
    yunet_model: str = ""
    sface_model: str = ""

    @property
    def models_ready(self) -> bool:
        return bool(self.yunet_model and self.sface_model)

    @classmethod
    def from_settings(cls, settings: dict) -> AutonomousKnowledge:
        return cls(
            user_id=get_current_user_id(),
            match_threshold=float(settings.get("match_threshold", MATCH_THRESHOLD_DEFAULT)),
            preview_camera_index=int(settings.get("preview_camera_index", 0)),
            detect_camera_index=int(settings.get("detect_camera_index", 0)),
            auto_detect_enabled=bool(settings.get("auto_detect_enabled", False)),
            yunet_model=str(FACE_MODEL_PATH) if FACE_MODEL_PATH.is_file() else "",
            sface_model=str(SFACE_MODEL_PATH) if SFACE_MODEL_PATH.is_file() else "",
        )


# Краткая выжимка для логов, UI и документации.
AUTONOMOUS_SUMMARY_RU = """
Автономная разблокировка профиля
--------------------------------
Знает: пользователь ОС, эталоны лица (faces + vault), порог match_threshold,
       камеру детекции, флаг auto_detect_enabled, пути к YuNet и SFace.

Делает: детекция лица → эмбеддинг → сравнение с галереей пользователя →
        при score ≥ порога разблокирует профиль (имя владельца эталона).

Не делает без эталонов, моделей, камеры или при выключенной автономии.
""".strip()
