import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SERVICE_API_KEY = _require("SERVICE_API_KEY")
FLASK_SECRET_KEY = _require("FLASK_SECRET_KEY")
ADMIN_PASSWORD = _require("ADMIN_PASSWORD")

EXECUTION_TIMEOUT_SECONDS = float(os.getenv("EXECUTION_TIMEOUT_SECONDS", "30"))
MAX_CODE_BYTES = int(os.getenv("MAX_CODE_BYTES", str(256 * 1024)))
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))

_database_file = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "sqlite3.db")))
DATABASE_PATH = str(_database_file)
DATABASE_URL = f"sqlite:///{_database_file.as_posix()}"


def _resolve_python_executable() -> str:
    configured = (os.getenv("PYTHON_EXECUTABLE") or "").strip()
    if configured:
        return configured
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("uwsgi"):
        for candidate in ("python3", "python"):
            found = shutil.which(candidate)
            if found:
                return found
    return exe


PYTHON_EXECUTABLE = _resolve_python_executable()


def _resolve_node_executable() -> str:
    configured = (os.getenv("NODE_EXECUTABLE") or "").strip()
    if configured:
        return configured
    return shutil.which("node") or shutil.which("nodejs") or "node"


NODE_EXECUTABLE = _resolve_node_executable()
