from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    if args.health:
        print("ok")
        return

    if not args.output:
        parser.error("--output is required")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps({"status": "success", "plugin": "sample_echo"}), encoding="utf-8")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["plugin", "status"])
        writer.writerow(["sample_echo", "success"])


if __name__ == "__main__":
    main()
