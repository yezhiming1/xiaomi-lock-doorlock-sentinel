#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    temporary = target.with_suffix(target.suffix + ".download")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "doorlock-sentinel-model-installer/0.0.5"},
    )
    with urllib.request.urlopen(request, timeout=600) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    temporary.replace(target)


def locate(archive: zipfile.ZipFile, name: str) -> str:
    matches = [item for item in archive.namelist() if Path(item).name == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name} in model package")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the pinned InsightFace model pair")
    parser.add_argument("--directory", type=Path, default=ROOT / "models")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--lock", type=Path, default=ROOT / "models.lock.json")
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    package = lock["package"]
    args.directory.mkdir(parents=True, exist_ok=True)
    archive_path = args.package or args.directory / package["name"]
    if not archive_path.is_file():
        download(package["url"], archive_path)
    if archive_path.stat().st_size != package["size_bytes"]:
        raise SystemExit("model package size mismatch")
    actual_package = sha256(archive_path)
    if actual_package != package["sha256"]:
        raise SystemExit("model package checksum mismatch")

    with zipfile.ZipFile(archive_path) as archive:
        for model in lock["models"]:
            expected = model["sha256"]
            expected_size = model["size_bytes"]
            if len(expected) != 64:
                raise SystemExit(f"model lock is incomplete for {model['name']}")
            target = args.directory / model["name"]
            if (
                target.is_file()
                and target.stat().st_size == expected_size
                and sha256(target) == expected
            ):
                print(f"verified {target.name}")
                continue
            member = locate(archive, model["name"])
            temporary = target.with_suffix(target.suffix + ".extract")
            with archive.open(member) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if temporary.stat().st_size != expected_size or sha256(temporary) != expected:
                temporary.unlink(missing_ok=True)
                raise SystemExit(f"model checksum mismatch for {model['name']}")
            temporary.replace(target)
            print(f"installed {target.name}")


if __name__ == "__main__":
    main()
