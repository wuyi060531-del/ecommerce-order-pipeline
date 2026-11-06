"""Verify sealed source files, local Markdown links and common private material.

Read-only. SHA-256 detects accidental changes; this is not a signed provenance
statement or a guarantee that all secret formats have been detected.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = {".git", ".venv", ".local", "logs", "__pycache__", ".pytest_cache", ".ruff_cache"}
PATTERNS = {
    "private_path": r"/(?:Users|home)/[^/\s\"']+|[A-Z]:\\Users\\[^\\\s]+",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "token": r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{10,})",
    "email": r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    "credential_literal": r"(?i)\b(?:MYSQL_(?:ROOT_)?PASSWORD|password|passwd|secret|api_key|access_token)[\"']?\s*[:=]\s*[\"']?[^\s\"',)}]{4,}",
    "database_url": r"(?:mysql|postgres(?:ql)?)://[^\s:/]+:[^\s@]+@",
    "phone": r"(?<![\w])1[3-9]\d{9}(?![\w])",
    "identity": r"(?<![\w])[1-9]\d{16}[\dXx](?![\w])",
}


def source_files(root: Path) -> dict[str, Path]:
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in RUNTIME_DIRS for part in relative.parts) or relative.parts[:2] == ("data", "processed"):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink cannot enter release: {relative}")
        if path.is_file():
            result[relative.as_posix()] = path
    return result


def verify_manifest(root: Path) -> dict[str, Path]:
    expected = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))["files"]
    actual = source_files(root)
    actual.pop("SHA256SUMS.json", None)
    if set(expected) != set(actual):
        raise ValueError("Release file set differs from SHA256SUMS.json")
    for name, path in actual.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
            raise ValueError(f"Release hash mismatch: {name}")
    return actual


def scan(root: Path) -> list[dict]:
    findings = []
    for name, path in source_files(root).items():
        if (name.startswith(("learning/", "src/day", "docs/history/"))
                or name in {"AGENTS.md", "docs/progress.md", "docs/learning_state.json"}):
            raise ValueError(f"Private learning material in release: {name}")
        payload = path.read_text(encoding="utf-8")
        for number, line in enumerate(payload.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if re.search(pattern, line):
                    findings.append(dict(path=name, line=number, category=category,
                                         line_sha256=hashlib.sha256(line.encode()).hexdigest()))
    return findings


def verify_release(root: Path) -> dict:
    files = verify_manifest(root)
    allowed = json.loads((root / "docs/SECURITY_ALLOWLIST.json").read_text(encoding="utf-8"))
    hits = scan(root)
    def signature(item: dict) -> tuple[str, str, str]:
        return item["path"], item["category"], item["line_sha256"]
    expected = {signature(item) for item in allowed}
    found = {signature(item) for item in hits}
    if found != expected:
        raise ValueError("Secret/private scan differs from reviewed exact-line allowlist")
    missing = []
    for name, path in files.items():
        if not name.endswith(".md"):
            continue
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.split("#")[0]
            if not target or re.match(r"\w+://", target):
                continue
            resolved = path.parent.joinpath(target).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                missing.append((name, target))
    if missing:
        raise ValueError(f"Broken or external local links: {missing}")
    return dict(manifest_files=len(files), unreviewed_findings=0, reviewed_exact_line_findings=len(hits),
                broken_internal_links=0, scan_scope="sealed source; runtime environment and outputs excluded")


if __name__ == "__main__":
    try:
        print(json.dumps(verify_release(ROOT), indent=2))
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from None
