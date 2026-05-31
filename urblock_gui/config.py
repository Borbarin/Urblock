import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
APP_VERSION = "1.0.0"
_data_override = os.environ.get("URBLOCK_DATA_DIR")
DATA_DIR = Path(_data_override).resolve() if _data_override else APP_DIR / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"  # устаревшее (миграция в users/<логин>/snapshots/)
BIOMETRICS_DIR = DATA_DIR / "biometrics"  # устаревшее хранилище (миграция)
USERS_DIR = DATA_DIR / "users"
VAULT_SUFFIX = ".vault"
EMBEDDING_VAULT_SUFFIX = ".emb.vault"
SNAPSHOT_VAULT_SUFFIX = ".snap.vault"
FACES_VAULT_FILE = "faces.vault"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"  # устаревшее (миграция)
FACES_FILE = DATA_DIR / "faces.json"  # устаревшее (миграция)
SETTINGS_FILE = DATA_DIR / "settings.json"
FACE_MODEL_PATH = APP_DIR / "models/face_detection_yunet_2023mar.onnx"
SFACE_MODEL_PATH = APP_DIR / "models/face_recognition_sface_2021dec.onnx"

FRAME_WIDTH = 640
FRAME_HEIGHT = 480
AUTO_START_CAMERA = False
# Порог cosine similarity для OpenCV FaceRecognizerSF (выше = похожее)
MATCH_THRESHOLD_DEFAULT = 0.35
# При загрузке: если .npy не совпадает со снимком/vault — пересобрать
EMBEDDING_SELF_CHECK_MIN = 0.85

DEFAULT_SETTINGS = {
    "preview_camera_index": 0,
    "detect_camera_index": 0,
    "frame_width": FRAME_WIDTH,
    "frame_height": FRAME_HEIGHT,
    "auto_start_camera": AUTO_START_CAMERA,
    "auto_detect_enabled": False,
    "face_box_color": [0, 255, 0],
    "match_threshold": MATCH_THRESHOLD_DEFAULT,
    "login_verify_timeout": 4.0,
    "eula_accepted": False,
    "eula_accepted_at": None,
}
