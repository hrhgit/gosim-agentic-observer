#!/usr/bin/env python3
"""Recover received protocol text from agent.log without rewriting its JSON."""
import argparse
import json
from pathlib import Path

PREFIX = "SURVEY26_INPUT_V1 "


def extract(source, destination):
    expected = 1
    count = 0
    for line in source:
        if not line.startswith(PREFIX):
            continue
        record = json.loads(line[len(PREFIX):])
        if record["received_sequence"] != expected:
            raise ValueError(f"input log gap: expected {expected}, got {record['received_sequence']}")
        raw = record["raw"]
        if not isinstance(raw, str):
            raise ValueError("raw input must be text")
        destination.write(raw)
        expected += 1
        count += 1
    if not count:
        raise ValueError("no SURVEY26_INPUT_V1 records; this log may predate input recording")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.log.resolve() == args.output.resolve():
        parser.error("output must differ from the source log")
    with args.log.open(encoding="utf-8", newline="") as source:
        with args.output.open("x", encoding="utf-8", newline="") as destination:
            count = extract(source, destination)
    print(f"Recovered {count} input lines into {args.output}")


if __name__ == "__main__":
    main()
