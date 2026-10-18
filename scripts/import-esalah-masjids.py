#!/usr/bin/env python3
"""Enrich the US catalog through eSalah's documented public tier (<=60/min)."""

import datetime as dt
import argparse
import json
from pathlib import Path
import re
import urllib.parse
from masjid_data.sources import Fetcher, STATE_CODES, clean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    args = parser.parse_args()
    work = args.work_dir
    fetch = Fetcher(work / "http-cache", delay=1.1, max_age=604800)
    listings = []
    for offset in range(0, 10001, 100):
        query = urllib.parse.urlencode(
            {
                "type": "mosque",
                "country": "united-states",
                "limit": 100,
                "offset": offset,
            }
        )
        result = fetch.json("https://esalah.com/api/v1/places.json?" + query)
        rows = result.get("results", result.get("places", []))
        listings.extend(rows)
        print(f"eSalah directory {len(listings)} listings", flush=True)
        if len(rows) < 100:
            break
    rows, errors = [], []
    for index, item in enumerate(listings):
        try:
            r = fetch.json(f'https://esalah.com/api/v1/places/{int(item["id"])}.json')
            if (
                r.get("country", {}).get("iso2") != "US"
                or not isinstance(r.get("lat"), (int, float))
                or not isinstance(r.get("lng"), (int, float))
            ):
                continue
            country = r.get("country") or {}
            region = r.get("region") or {}
            city = r.get("city") or {}
            region = region.get("name", "") if isinstance(region, dict) else region
            city = city.get("name", "") if isinstance(city, dict) else city
            if re.search(
                r"\b(hindu|baps|swaminarayan|mandir|shriners?|masonic)\b",
                r["name"],
                re.I,
            ):
                continue
            row = {
                "id": f'esalah/{r["id"]}',
                "name": r["name"],
                "address": clean(r.get("address")) or "",
                "city": city,
                "region": STATE_CODES.get(region, region),
                "country": "US",
                "lat": r["lat"],
                "lng": r["lng"],
                "website": clean(r.get("website")),
                "phone": clean(r.get("phone")),
                "sourceUrl": r["url"],
                "sources": [
                    {"provider": "esalah", "id": str(r["id"]), "url": r["url"]}
                ],
            }
            rows.append(row)
        except RuntimeError:
            raise
        except Exception as error:
            errors.append({"id": item["id"], "error": str(error)})
        if index % 25 == 0:
            print(
                f"eSalah enrichment {index+1}/{len(listings)}: {len(rows)} source profiles",
                flush=True,
            )
    output = {
        "importedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "masjids": rows,
        "listingCount": len(listings),
        "errors": errors,
    }
    path = work / "esalah.json"
    if len(errors) > max(10, len(listings) // 20) or len(rows) < 1500:
        raise ValueError("Incomplete eSalah import; previous snapshot preserved")
    if path.exists() and len(rows) < len(json.loads(path.read_text())["masjids"]) * 0.8:
        raise ValueError("Unexpected directory shrink; previous snapshot preserved")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)
    print(
        f"eSalah enrichment complete: {len(rows)} profiles, {len(errors)} unavailable",
        flush=True,
    )


if __name__ == "__main__":
    main()
