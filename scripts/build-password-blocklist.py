#!/usr/bin/env python3

import argparse
import hashlib
import unicodedata
from pathlib import Path

MIN_PASSWORD_LENGTH = 15


def build_blocklist(source: Path, destination: Path) -> int:
    digests: set[bytes] = set()
    with source.open(encoding="ascii") as handle:
        for line in handle:
            password = line.rstrip("\r\n")
            if len(password) < MIN_PASSWORD_LENGTH:
                continue
            normalized = unicodedata.normalize("NFC", password).casefold()
            digests.add(hashlib.sha256(normalized.encode("utf-8")).digest())
    destination.write_bytes(b"".join(sorted(digests)))
    return len(digests)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build CaloGraph's binary common-password digest index."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    count = build_blocklist(args.source, args.destination)
    print(f"Wrote {count} password digests to {args.destination}")


if __name__ == "__main__":
    main()
