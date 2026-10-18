#!/usr/bin/env python3
"""Fetch unambiguous, mosque-embedded widgets discovered on official sites."""

import datetime as dt
import argparse
import html
import importlib.util
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from masjid_data.sources import Fetcher

spec = importlib.util.spec_from_file_location(
    "fetch_schedules", Path(__file__).with_name("fetch-masjid-schedules.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def text(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]*>", " ", value))).strip()


def parse_mohid(page, url, now, zone):
    prayers, iqamah, sessions = {}, {}, {}
    for row in re.findall(r"<li\b[^>]*>([\s\S]*?)</li>", page, re.I):
        label = text(re.split(r"<div\b", row, flags=re.I)[0])

        def column(name):
            match = re.search(
                r'<div[^>]*class=["\x27][^"\x27]*'
                + name
                + r'[^"\x27]*["\x27][^>]*>([\s\S]*?)</div>',
                row,
                re.I,
            )
            return text(match[1]) if match else None

        value = module.parse_time(column("prayer_iqama_div"))
        if label in ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]:
            start = module.parse_time(column("prayer_azaan_div"))
            if start is not None:
                prayers[label] = start
            if value is not None:
                iqamah[label] = value
            elif start is not None:
                offset = re.fullmatch(
                    r"After\s+(\d{1,2})\s+min(?:utes)?",
                    column("prayer_iqama_div") or "",
                    re.I,
                )
                if offset:
                    iqamah[label] = (start + int(offset[1])) % 1440
        elif value is not None:
            match = re.fullmatch(
                r"Friday\s+(Khutba[h]?|Iqama[h]?)\s+(\d+)", label, re.I
            )
            if match:
                sessions.setdefault(int(match[2]), {})[
                    "khutbah" if match[1].lower().startswith("khut") else "iqamah"
                ] = value
    if not iqamah and not sessions:
        raise ValueError("Widget has no congregation times")
    friday = [value for key, value in sorted(sessions.items())]
    return {
        "provider": "mohid",
        "providerId": url,
        "sourceUrl": url,
        "date": now.astimezone(ZoneInfo(zone)).date().isoformat(),
        "retrievedAt": now.isoformat(),
        "iqamah": iqamah,
        "prayers": prayers,
        "fridaySessions": friday,
        "jumuah": [s.get("khutbah", s.get("iqamah")) for s in friday],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    parser.add_argument(
        "--catalog", type=Path, default=Path("assets/data/masjids/us.json")
    )
    args = parser.parse_args()
    work = args.work_dir
    fetch = Fetcher(work / "http-cache", delay=0.8, max_age=21600)
    directory = {r["id"]: r for r in json.loads(args.catalog.read_text())["masjids"]}
    discovery = json.loads((work / "discovery.json").read_text())
    path = work / "schedules.json"
    snapshots = json.loads(path.read_text())
    added = 0
    for row in discovery["results"]:
        mosque = directory.get(row["id"])
        if not mosque:
            continue
        for provider, identity in row.get("providers", {}).items():
            try:
                now = dt.datetime.now(dt.timezone.utc)
                if provider == "mohid":
                    page = fetch.get(identity)
                    retrieved = dt.datetime.fromisoformat(fetch.retrieved_at(identity))
                    value = parse_mohid(page, identity, retrieved, mosque["zone"])
                elif provider == "masjidal":
                    if identity in snapshots["schedules"]:
                        continue
                    url = "https://masjidal.com/api/v1/time?masjid_id=" + identity
                    payload = fetch.json(url)
                    value = module.parse_masjidal(
                        payload, identity, fetch.retrieved_at(url)
                    )
                else:
                    continue
                snapshots["schedules"][identity] = value
                added += 1
            except RuntimeError:
                raise
            except Exception as error:
                snapshots["errors"].append(
                    {"provider": provider, "id": identity, "error": str(error)}
                )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
    print(f"Additional published widget schedules: {added}", flush=True)


if __name__ == "__main__":
    main()
