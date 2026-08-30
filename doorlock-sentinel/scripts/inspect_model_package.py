#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


def digest_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Print hashes for the two required ONNX files")
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.package) as archive:
        for name in ("det_2.5g.onnx", "w600k_r50.onnx"):
            matches = [item for item in archive.namelist() if Path(item).name == name]
            if len(matches) != 1:
                raise SystemExit(f"expected exactly one {name}")
            with archive.open(matches[0]) as stream:
                print(f"{name} {digest_stream(stream)}")


if __name__ == "__main__":
    main()
