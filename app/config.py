import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(REPO_ROOT / 'data' / 'sheet.db').as_posix()}")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(REPO_ROOT / "uploads")))

MAX_AVATAR_BYTES = 8 * 1024 * 1024
AVATAR_MAX_DIM = 512
AVATAR_THUMB_DIM = 128
