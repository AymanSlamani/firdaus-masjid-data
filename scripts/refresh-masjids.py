#!/usr/bin/env python3
"""Import public directory sources and build a reproducible, audited catalog."""

import argparse
import datetime as dt
import json
from pathlib import Path
from masjid_data.sources import Fetcher, import_usa_directory, import_masjidal_directory


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    parser.add_argument("--source", choices=["all", "usa", "masjidal"], default="all")
    args = parser.parse_args()
    fetch = Fetcher(args.work_dir / "http-cache")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if args.source in {"all", "usa"}:
        rows, rejected = import_usa_directory(fetch)
        write_json(
            args.work_dir / "usa.json",
            {"importedAt": now, "masjids": rows, "rejected": rejected},
        )
        print(
            f"US directory accepted {len(rows)}, review/excluded {len(rejected)}",
            flush=True,
        )
    if args.source in {"all", "masjidal"}:
        rows = import_masjidal_directory(fetch)
        write_json(
            args.work_dir / "masjidal.json", {"importedAt": now, "masjids": rows}
        )
        print(f"Masjidal US locations {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
