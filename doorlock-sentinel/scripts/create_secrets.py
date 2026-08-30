#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import secrets
import string
from pathlib import Path

from argon2 import PasswordHasher


def create_exclusive(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.write("\n")


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create untracked Doorlock Sentinel secrets")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--password-output", type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    os.chmod(args.directory, 0o700)

    generated = []
    for name in ("internal_api_secret", "security_pepper"):
        path = args.directory / name
        if not path.exists():
            create_exclusive(path, secrets.token_hex(32))
            generated.append(name)

    password_hash = args.directory / "web_password_hash"
    if not password_hash.exists():
        password = random_password()
        hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
        )
        create_exclusive(password_hash, hasher.hash(password))
        args.password_output.parent.mkdir(parents=True, exist_ok=True)
        create_exclusive(args.password_output, password)
        generated.extend(["web_password_hash", "initial_password"])
    print("created=" + ",".join(generated) if generated else "created=none")


if __name__ == "__main__":
    main()
