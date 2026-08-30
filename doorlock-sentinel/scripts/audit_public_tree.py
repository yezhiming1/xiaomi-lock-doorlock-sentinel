#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BLOCKED_SUFFIXES = {
    ".7z",
    ".avi",
    ".bin",
    ".db",
    ".jpeg",
    ".jpg",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".onnx",
    ".png",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".webm",
    ".zip",
}
ALLOWED_SECRET_TEST_WORDS = {
    "development",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "replace",
    "smoke",
    "test",
}
TEXT_RULES = {
    "WINDOWS_PROFILE_PATH": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.I),
    "NAS_PRIVATE_PATH": re.compile(r"/(?:data|volume)_s?\d{2,}(?:/|\b)", re.I),
    "RFC1918_ADDRESS": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
    "LOCAL_HA_HOST": re.compile(r"\bhomeassistant\.local\b", re.I),
    "PRIVATE_STYLE_DOMAIN": re.compile(r"\b[a-z0-9-]+\.\d{5,}\.xyz\b", re.I),
    "BEARER_VALUE": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
}
SECRET_ASSIGNMENT = re.compile(
    r"(?m)^\s*(?:[A-Z0-9_]*(?:PASSWORD|TOKEN|SECRET|COOKIE|API_KEY|PEPPER))"
    r"\s*[:=]\s*['\"]?([^'\"\s#]+)"
)


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    code: str
    line: int | None = None


def candidate_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return [
        PROJECT_ROOT / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def safe_test_secret(value: str) -> bool:
    lowered = value.lower()
    return (
        value in {"", "..."}
        or lowered.endswith(".invalid")
        or any(word in lowered for word in ALLOWED_SECRET_TEST_WORDS)
    )


def scan(path: Path) -> list[Finding]:
    relative = path.relative_to(PROJECT_ROOT).as_posix()
    findings: list[Finding] = []
    if path.is_symlink():
        return [Finding(relative, "SYMLINK")]
    lowered_parts = {part.lower() for part in path.parts}
    if "secrets" in lowered_parts and path.name != ".gitkeep":
        findings.append(Finding(relative, "SECRET_DIRECTORY_CONTENT"))
    suffix = path.suffix.lower()
    if suffix in BLOCKED_SUFFIXES or path.name.lower().endswith(".tar.gz"):
        findings.append(Finding(relative, "BLOCKED_BINARY_OR_PRIVATE_ARTIFACT"))
        return findings
    if path.stat().st_size > 5 * 1024 * 1024:
        findings.append(Finding(relative, "UNEXPECTED_LARGE_FILE"))
        return findings
    raw = path.read_bytes()
    if b"\0" in raw:
        findings.append(Finding(relative, "UNEXPECTED_BINARY_FILE"))
        return findings
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        findings.append(Finding(relative, "NON_UTF8_TEXT"))
        return findings
    for code, pattern in TEXT_RULES.items():
        match = pattern.search(text)
        if match:
            findings.append(Finding(relative, code, text.count("\n", 0, match.start()) + 1))
    for match in SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1).strip()
        if not safe_test_secret(value):
            findings.append(
                Finding(
                    relative,
                    "NON_PLACEHOLDER_SECRET_ASSIGNMENT",
                    text.count("\n", 0, match.start()) + 1,
                )
            )
    return findings


def main() -> None:
    findings: list[Finding] = []
    files = candidate_files()
    for path in files:
        if path.is_file() or path.is_symlink():
            findings.extend(scan(path))
    if findings:
        for finding in sorted(findings, key=lambda item: (item.path, item.code, item.line or 0)):
            location = f":{finding.line}" if finding.line else ""
            print(f"PUBLIC_TREE_FAIL {finding.code} {finding.path}{location}")
        raise SystemExit(1)
    print(f"PUBLIC_TREE_PASS files={len(files)}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        print("PUBLIC_TREE_FAIL GIT_FILE_ENUMERATION", file=sys.stderr)
        raise SystemExit(2) from None
