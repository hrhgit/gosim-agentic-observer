#!/usr/bin/env python3
"""Decode V2 captures; output records explicitly mark omitted public target rows."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zlib

PREFIX = "SURVEY26_CAPTURE_V2 "


def extract(source, destination):
    expected = 1
    for line in source:
        if not line.startswith(PREFIX):
            continue
        packet = json.loads(line[len(PREFIX):])
        if packet["first"] != expected:
            raise ValueError(f"capture gap: expected {expected}, got {packet['first']}")
        raw = zlib.decompress(base64.b64decode(packet["data"], validate=True))
        if hashlib.sha256(raw).hexdigest() != packet["sha256"]:
            raise ValueError("capture checksum mismatch")
        records = raw.decode("ascii").splitlines(keepends=True)
        if len(records) != packet["count"] or packet["last"] != expected + len(records) - 1:
            raise ValueError("capture count mismatch")
        for record in records:
            if json.loads(record)["received_sequence"] != expected:
                raise ValueError("capture record sequence mismatch")
            destination.write(record)
            expected += 1
    if expected == 1:
        raise ValueError("no V2 capture records found")
    return expected - 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.log.resolve() == args.output.resolve():
        parser.error("output must differ from source")
    with args.log.open(encoding="utf-8") as source, args.output.open("x", encoding="utf-8") as destination:
        count = extract(source, destination)
    print(f"Recovered {count} capture records into {args.output}")


if __name__ == "__main__":
    main()
