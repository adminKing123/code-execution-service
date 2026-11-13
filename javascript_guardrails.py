"""Static guardrails for user JavaScript (checked before spawn)."""
from __future__ import annotations

import re

BLOCKED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brequire\s*\(", re.I), "require() is not allowed"),
    (re.compile(r"\bimport\s+"), "import statements are not allowed"),
    (re.compile(r"\bexport\s+"), "export statements are not allowed"),
    (re.compile(r"\beval\s*\(", re.I), "eval() is not allowed"),
    (re.compile(r"\bnew\s+Function\s*\(", re.I), "new Function() is not allowed"),
    (re.compile(r"\bchild_process\b"), "child_process is not allowed"),
    (re.compile(r"\bnode:\w+"), "node: built-in modules are not allowed"),
    (re.compile(r"\bprocess\s*\."), "process is not allowed"),
    (re.compile(r"\bglobalThis\s*\.\s*process\b"), "process is not allowed"),
    (re.compile(r"\b__dirname\b"), "__dirname is not allowed"),
    (re.compile(r"\b__filename\b"), "__filename is not allowed"),
    (re.compile(r"\bfs\s*\."), "fs module access is not allowed"),
    (re.compile(r"\bDeno\."), "Deno is not allowed"),
    (re.compile(r"\bBun\."), "Bun is not allowed"),
    (re.compile(r"\bWebAssembly\b"), "WebAssembly is not allowed"),
    (re.compile(r"\bfetch\s*\("), "fetch() is not allowed"),
    (re.compile(r"\bXMLHttpRequest\b"), "XMLHttpRequest is not allowed"),
)


class GuardrailViolation(ValueError):
    pass


def validate_javascript_source(source: str) -> None:
    if not source or len(source) == 0:
        raise GuardrailViolation("code is required")
    for pattern, message in BLOCKED_PATTERNS:
        if pattern.search(source):
            raise GuardrailViolation(message)
