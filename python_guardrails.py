"""Static and runtime guardrails for user Python code."""
from __future__ import annotations

import ast
from typing import Iterable

# Modules that must not be imported (top-level package name).
BLOCKED_IMPORT_ROOTS = frozenset(
    {
        "os",
        "sys",
        "pathlib",
        "shutil",
        "subprocess",
        "multiprocessing",
        "socket",
        "ssl",
        "ctypes",
        "pty",
        "fcntl",
        "mmap",
        "resource",
        "signal",
        "threading",
        "asyncio",
        "concurrent",
        "ftplib",
        "telnetlib",
        "smtplib",
        "xmlrpc",
        "http",
        "urllib",
        "webbrowser",
        "pickle",
        "shelve",
        "marshal",
        "builtins",
        "importlib",
        "pip",
        "venv",
        "ensurepip",
        "runpy",
        "code",
        "codeop",
        "pty",
        "tty",
        "termios",
        "winreg",
        "wmi",
        "msvcrt",
    }
)

# Builtins removed from the execution namespace (use safe shims instead).
BLOCKED_BUILTINS = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "input",  # replaced with stdin shim
    }
)

# Allowed top-level import roots (empty = only stdlib not on blocklist; explicit allow optional).
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "math",
        "cmath",
        "decimal",
        "fractions",
        "random",
        "statistics",
        "json",
        "re",
        "datetime",
        "calendar",
        "collections",
        "itertools",
        "functools",
        "operator",
        "heapq",
        "bisect",
        "array",
        "struct",
        "copy",
        "enum",
        "dataclasses",
        "typing",
        "uuid",
        "hashlib",
        "hmac",
        "secrets",
        "base64",
        "binascii",
        "codecs",
        "string",
        "textwrap",
        "unicodedata",
        "pprint",
        "time",
        "abc",
        "contextlib",
        "numbers",
        "io",
    }
)


class GuardrailViolation(ValueError):
    pass


def _root_name(name: str) -> str:
    return (name or "").split(".")[0]


def _check_import_name(name: str, *, lineno: int | None) -> None:
    root = _root_name(name)
    if root in BLOCKED_IMPORT_ROOTS:
        raise GuardrailViolation(
            f"Import of '{name}' is not allowed (line {lineno or '?'})"
        )


def validate_python_source(source: str) -> None:
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise GuardrailViolation(f"Syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import_name(alias.name, lineno=node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _check_import_name(node.module, lineno=node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("exec", "eval", "compile", "__import__", "open"):
                raise GuardrailViolation(
                    f"Call to '{node.func.id}()' is not allowed (line {node.lineno})"
                )


def build_safe_builtins(*, stdin_lines: Iterable[str]) -> dict:
    import builtins

    queue = list(stdin_lines)
    index = 0

    def safe_input(prompt: str = "") -> str:
        nonlocal index
        if index >= len(queue):
            raise EOFError(
                "input() called but no more stdin_lines were provided in the API request"
            )
        line = queue[index]
        index += 1
        return line

    safe: dict = {}
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        if name in BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(builtins, name)
    safe["input"] = safe_input

    def safe_import(
        name: str,
        globals=None,
        locals=None,
        fromlist: tuple = (),
        level: int = 0,
    ):
        if level != 0:
            raise ImportError("relative imports are not allowed")
        _check_import_name(name, lineno=None)
        for item in fromlist or ():
            _check_import_name(f"{name}.{item}" if name else str(item), lineno=None)
        return _import_builtin(name, globals, locals, fromlist, level)

    safe["__import__"] = safe_import
    return safe


def _import_builtin(name, globals, locals, fromlist, level):
    import builtins as bi

    return bi.__import__(name, globals, locals, fromlist, level)


def safe_globals(*, stdin_lines: Iterable[str] | None = None) -> dict:
    lines = stdin_lines or []
    return {
        "__builtins__": build_safe_builtins(stdin_lines=lines),
        "__name__": "__user_code__",
        "__doc__": None,
    }
