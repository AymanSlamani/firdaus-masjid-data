#!/usr/bin/env python3
"""Cache the provider's dated current schedule for every imported US account."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
from pathlib import Path
import re
import threading
import urllib.parse
from masjid_data.sources import Fetcher


def parse_time(value):
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)?\s*", value, re.I)
    if not m:
        return None
    h, minute = int(m[1]), int(m[2])
    period = m[3]
    if (
        minute > 59
        or (period is None and h > 23)
        or (period is not None and not 1 <= h <= 12)
    ):
        return None
    return (
        h % 12 + (12 if period.upper() == "PM" else 0) if period else h
    ) * 60 + minute


def parse_masjidal(payload, identity, retrieved_at):
    if payload.get("status") != "success":
        raise ValueError("Provider reports no schedule")
    data = payload["data"]
    date = data.get("date")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Missing schedule date")
    dt.date.fromisoformat(date)
    mapping = {
        "fajr": "Fajr",
        "zuhr": "Dhuhr",
        "dhuhr": "Dhuhr",
        "asr": "Asr",
        "maghrib": "Maghrib",
        "isha": "Isha",
    }
    iqamah = {
        mapping[k]: v
        for k, text in (data.get("iqama") or {}).items()
        if k in mapping and (v := parse_time(text)) is not None
    }
    prayers = {
        mapping[k]: v
        for k, text in (data.get("salah") or {}).items()
        if k in mapping and (v := parse_time(text)) is not None
    }
    friday = sorted(
        {
            v
            for k, text in (data.get("iqama") or {}).items()
            if re.fullmatch(r"jummah\d*", k) and (v := parse_time(text)) is not None
        }
    )
    issues = []
    if (
        iqamah.get("Fajr") is not None
        and prayers.get("Dhuhr") is not None
        and iqamah["Fajr"] >= prayers["Dhuhr"]
    ):
        iqamah.pop("Fajr")
        issues.append(
            "Source Fajr iqamah is later than its midday prayer; confirm with the masjid."
        )
    if 0 in friday:
        friday.remove(0)
        issues.append("An unset midnight Friday session was omitted.")
    if not iqamah and not friday:
        raise ValueError("Provider has no published congregation times")
    return {
        "provider": "masjidal",
        "providerId": identity,
        "date": date,
        "retrievedAt": retrieved_at,
        "sourceUrl": f"https://masjidal.com/widget/monthly/v2/?masjid_id={identity}",
        "iqamah": iqamah,
        "prayers": prayers,
        "jumuah": friday,
        "qualityIssues": issues,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    p.add_argument("--limit", type=int)
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    fetch = Fetcher(a.work_dir / "http-cache", delay=0.6, max_age=21600)
    rows = json.loads((a.work_dir / "masjidal.json").read_text())["masjids"]
    rows = [r for r in rows if not r.get("shellAccount")]
    if a.limit:
        rows = rows[: a.limit]
    snapshots, errors = {}, []
    stop = threading.Event()

    def one(row):
        if stop.is_set():
            return None
        identity = row["providers"]["masjidal"]
        url = "https://masjidal.com/api/v1/time?" + urllib.parse.urlencode(
            {"masjid_id": identity}
        )
        try:
            payload = fetch.json(url)
            return (
                identity,
                parse_masjidal(payload, identity, fetch.retrieved_at(url)),
                None,
            )
        except RuntimeError:
            stop.set()
            raise
        except Exception as error:
            return identity, None, str(error)

    try:
        with ThreadPoolExecutor(max_workers=min(max(a.workers, 1), 4)) as pool:
            futures = [pool.submit(one, row) for row in rows]
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result is None:
                    continue
                identity, value, error = result
                if value:
                    snapshots[identity] = value
                else:
                    errors.append(
                        {"provider": "masjidal", "id": identity, "error": error}
                    )
                if index % 25 == 0 or index == len(rows):
                    print(
                        f"Schedules {index}/{len(rows)}: {len(snapshots)} published, {len(errors)} absent/errors",
                        flush=True,
                    )
    finally:
        result = {
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "schedules": snapshots,
            "errors": errors,
            "attempted": len(snapshots) + len(errors),
            "complete": not stop.is_set() and len(snapshots) + len(errors) == len(rows),
        }
        hard_errors = [
            e
            for e in errors
            if "HTTP Error" in e["error"]
            or "timed out" in e["error"]
            or "SSL" in e["error"]
        ]
        if (
            not result["complete"]
            or len(hard_errors) > max(20, len(rows) * 0.1)
            or len(snapshots) < len(rows) * 0.7
        ):
            diagnostic = a.work_dir / "schedule-failures.json"
            diagnostic.write_text(
                json.dumps(
                    {
                        "generatedAt": result["generatedAt"],
                        "attempted": result["attempted"],
                        "published": len(snapshots),
                        "errors": errors,
                    },
                    indent=2,
                )
                + "\n"
            )
            raise RuntimeError(
                "Provider import failed completeness checks; preserving the previous complete schedule snapshot"
            )
        path = a.work_dir / "schedules.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    print(f"Cached {len(snapshots)} published dated schedules", flush=True)


if __name__ == "__main__":
    main()
