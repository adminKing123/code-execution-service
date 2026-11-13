import platform

import config

SUPPORTED = {
    "python": {
        "id": "python",
        "name": "Python",
        "version": platform.python_version(),
        "file_extension": ".py",
        "description": "CPython execution in an isolated subprocess",
    },
}


def list_languages() -> list[dict]:
    items = []
    for lang in SUPPORTED.values():
        item = dict(lang)
        if item["id"] == "python":
            item["runtime_executable"] = config.PYTHON_EXECUTABLE
        items.append(item)
    return items


def get_language(language_id: str) -> dict | None:
    return SUPPORTED.get((language_id or "").strip().lower())
