#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise SystemExit(f"MODEL_LOCK_FAIL {label}_sha256")
    if any(character not in "0123456789abcdef" for character in value):
        raise SystemExit(f"MODEL_LOCK_FAIL {label}_sha256")
    return value


def size(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SystemExit(f"MODEL_LOCK_FAIL {label}_size")
    return value


def main() -> None:
    lock = json.loads((ROOT / "models.lock.json").read_text(encoding="utf-8"))
    if lock.get("schema") != 1 or lock.get("model_id") != "insightface-buffalo-m-det2.5g-r50-v1":
        raise SystemExit("MODEL_LOCK_FAIL identity")
    package = lock.get("package")
    if not isinstance(package, dict):
        raise SystemExit("MODEL_LOCK_FAIL package")
    source = urlparse(str(package.get("url", "")))
    if source.scheme != "https" or source.netloc != "github.com":
        raise SystemExit("MODEL_LOCK_FAIL package_source")
    digest(package.get("sha256"), "package")
    size(package.get("size_bytes"), "package")
    models = lock.get("models")
    if not isinstance(models, list) or {item.get("name") for item in models} != {
        "det_2.5g.onnx",
        "w600k_r50.onnx",
    }:
        raise SystemExit("MODEL_LOCK_FAIL model_set")
    for model in models:
        digest(model.get("sha256"), str(model.get("name")))
        size(model.get("size_bytes"), str(model.get("name")))
    license_data = lock.get("license")
    if (
        not isinstance(license_data, dict)
        or license_data.get("class") != "non-commercial-research-only"
    ):
        raise SystemExit("MODEL_LOCK_FAIL license")
    print("MODEL_LOCK_PASS models=2")


if __name__ == "__main__":
    main()
