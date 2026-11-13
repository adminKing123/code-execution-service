import platform
import subprocess

import config

SUPPORTED = {
    "python": {
        "id": "python",
        "name": "Python",
        "version": platform.python_version(),
        "file_extension": ".py",
        "description": "CPython execution in an isolated subprocess",
        "main_entry": "def main():",
    },
    "javascript": {
        "id": "javascript",
        "name": "JavaScript",
        "version": None,
        "file_extension": ".js",
        "description": "JavaScript (Node.js vm sandbox) in an isolated subprocess",
        "main_entry": "function main()",
    },
}


def _node_version() -> str:
    try:
        proc = subprocess.run(
            [config.NODE_EXECUTABLE, "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip().lstrip("v")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def list_languages() -> list[dict]:
    items = []
    for lang in SUPPORTED.values():
        item = dict(lang)
        if item["id"] == "python":
            item["runtime_executable"] = config.PYTHON_EXECUTABLE
            item["version"] = platform.python_version()
        elif item["id"] == "javascript":
            item["runtime_executable"] = config.NODE_EXECUTABLE
            item["version"] = _node_version()
        items.append(item)
    return items


def get_language(language_id: str) -> dict | None:
    return SUPPORTED.get((language_id or "").strip().lower())
