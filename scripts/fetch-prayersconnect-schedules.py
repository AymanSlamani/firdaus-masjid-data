#!/usr/bin/env python3
"""Fetch source-backed congregation times; reject the provider's calculated fallbacks."""

import argparse
import datetime as dt
import json
from pathlib import Path
from tzfpy import get_tz
from zoneinfo import ZoneInfo
from masjid_data.prayersconnect import (
    GuestClient,
    DETAIL_FIELDS,
    FRIDAY_FIELDS,
    parse_schedule,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    a = p.parse_args()
    rows = json.loads((a.work_dir / "prayersconnect.json").read_text())["masjids"]
    for row in rows:
        if not row.get("zone"):
            row["zone"] = get_tz(row["lng"], row["lat"])
    client = GuestClient(a.work_dir / "pc-http-cache")
    result, errors = {}, []
    for start in range(0, len(rows), 5):
        batch = rows[start : start + 5]
        now = dt.datetime.now(dt.timezone.utc)
        dates = [now.astimezone(ZoneInfo(r["zone"])).date().isoformat() for r in batch]
        query = (
            "{"
            + " ".join(
                f'd{i}:mosqueDetail(id:{r["providers"]["prayersconnect"]}){{{DETAIL_FIELDS}}} t{i}:prayerTimesByDate(mosqueId:{r["providers"]["prayersconnect"]},date:"{dates[i]}"){{{FRIDAY_FIELDS}}}'
                for i, r in enumerate(batch)
            )
            + "}"
        )
        try:
            data, retrieved = client.query(query)
            for i, r in enumerate(batch):
                identity = r["providers"]["prayersconnect"]
                detail = data.get("d" + str(i))
                if detail is None:
                    errors.append({"id": identity, "reason": "No public detail"})
                    continue
                snapshot = parse_schedule(
                    detail,
                    data.get("t" + str(i)),
                    identity,
                    dates[i],
                    retrieved,
                    r["zone"],
                )
                if snapshot:
                    result["prayersconnect/" + identity] = snapshot
        except RuntimeError:
            raise
        except Exception as error:
            errors.append({"offset": start, "reason": str(error)})
        if start % 100 == 0:
            print(
                f"PrayersConnect schedules {min(start+5,len(rows))}/{len(rows)}: {len(result)} source-backed timetables",
                flush=True,
            )
    if len(errors) > max(5, len(rows) // 50) or len(result) < 100:
        raise ValueError(
            f"Provider import incomplete: {len(errors)} errors, {len(result)} records; previous snapshot preserved"
        )
    path = a.work_dir / "schedules.json"
    payload = json.loads(path.read_text())
    payload["schedules"] = {
        k: v
        for k, v in payload["schedules"].items()
        if v.get("provider") != "prayersconnect"
    }
    payload["schedules"].update(result)
    payload["prayersconnectErrors"] = errors
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)
    print(
        f"PrayersConnect complete: {len(result)} timetables, {len(errors)} unavailable",
        flush=True,
    )


if __name__ == "__main__":
    main()
