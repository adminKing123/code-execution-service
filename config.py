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


def _nvm_node_candidates() -> list[str]:
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_root.is_dir():
        return []
    found = sorted(nvm_root.glob("*/bin/node"), key=lambda p: p.parent.parent.name, reverse=True)
    return [str(p) for p in found if p.is_file()]


def _node_candidates() -> list[str]:
    candidates: list[str] = []
    configured = (os.getenv("NODE_EXECUTABLE") or "").strip()
    if configured:
        candidates.append(configured)
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend(_nvm_node_candidates())
    candidates.extend(
        str(p)
        for p in (
            Path("/usr/local/bin/node"),
            Path("/usr/bin/node"),
        )
        if p.is_file()
    )
    unique: list[str] = []
    for path in candidates:
        if path and path not in unique:
            unique.append(path)
    return unique


def get_node_executable() -> str | None:
    """Full path to node, or None if not found (uWSGI often has no node on PATH)."""
    for path in _node_candidates():
        if Path(path).is_file():
            return path
    return None


def node_setup_hint() -> str:
    return (
        "JavaScript runtime not found. In a PythonAnywhere Bash console run: which node\n"
        "Then set NODE_EXECUTABLE to that full path in your Web app environment variables "
        "or .env and reload."
    )


# Back-compat alias for templates / languages
NODE_EXECUTABLE = get_node_executable() or ""
