#!/usr/bin/env python3
"""Import the public US directory using the provider's documented guest API."""

import argparse
import datetime as dt
import json
from pathlib import Path
from tzfpy import get_tz
from masjid_data.prayersconnect import GuestClient
from masjid_data.sources import STATE_CODES, clean


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    a = p.parse_args()
    client = GuestClient(a.work_dir / "pc-http-cache", max_age=604800)
    rows, rejected, ids = [], [], set()
    page = 1
    while True:
        query = 'query($p:Int!){quickListMosques(country:"United States",page:$p,limit:50){total totalPages hasNextPage data{id name address city country{name} province{name} latitude longitude timezone website phone zipcode hide hidden merged_into_id}}}'
        data, retrieved = client.query(query, {"p": page})
        result = data["quickListMosques"]
        for r in result["data"]:
            if r["id"] in ids:
                raise ValueError("Provider pagination repeated an identity")
            ids.add(r["id"])
            if (
                (r.get("country") or {}).get("name") != "United States"
                or r.get("hide")
                or r.get("hidden")
                or r.get("merged_into_id")
            ):
                continue
            if not isinstance(r.get("latitude"), (float, int)) or not isinstance(
                r.get("longitude"), (float, int)
            ):
                rejected.append({"id": r["id"], "reason": "Missing coordinates"})
                continue
            identity = str(r["id"])
            url = f"https://prayersconnect.com/mosques/{identity}"
            rows.append(
                {
                    "id": "prayersconnect/" + identity,
                    "name": r["name"].strip(),
                    "address": r.get("address") or "",
                    "city": r.get("city") or "",
                    "region": STATE_CODES.get(
                        (r.get("province") or {}).get("name"), ""
                    ),
                    "country": "US",
                    "lat": r["latitude"],
                    "lng": r["longitude"],
                    "zone": get_tz(r["longitude"], r["latitude"]),
                    "website": clean(r.get("website")),
                    "phone": clean(r.get("phone")),
                    "postalCode": clean(r.get("zipcode")),
                    "providers": {"prayersconnect": identity},
                    "sourceUrl": url,
                    "sources": [
                        {"provider": "prayersconnect", "id": identity, "url": url}
                    ],
                }
            )
        print(
            f'PrayersConnect directory {page}/{result["totalPages"]}: {len(rows)} US locations',
            flush=True,
        )
        if not result["hasNextPage"]:
            if len(ids) != result["total"]:
                raise ValueError("Provider pagination count does not match total")
            break
        page += 1
        if page > 500:
            raise ValueError("Unexpected pagination length")
    if len(rows) < 2500:
        raise ValueError(
            "Unexpectedly small national directory; preserve previous data"
        )
    value = {
        "importedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "masjids": rows,
        "rejected": rejected,
    }
    path = a.work_dir / "prayersconnect.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


if __name__ == "__main__":
    main()
