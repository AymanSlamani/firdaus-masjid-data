#!/usr/bin/env python3
"""Import a country OSM snapshot for offline discovery, without guessing schedules.

Run in a venv with tzfpy==2.1.1 installed. Use --input to rebuild from an
existing Overpass JSON response. Each run makes at most one Overpass request.
"""

import argparse
import json
from pathlib import Path
import urllib.parse
import urllib.request
from tzfpy import get_tz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="US")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    country = args.country.upper()
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        parser.error("country must be a two-letter ISO code")
    if args.input:
        raw = json.loads(args.input.read_text())
    else:
        query = (
            "[out:json][timeout:90];"
            f'area["ISO3166-1"="{country}"][admin_level=2]->.country;'
            "nwr(area.country)[amenity=place_of_worship][religion=muslim];"
            "out center tags;"
        )
        request = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": "Firdaus/1.0 directory-import"},
        )
        with urllib.request.urlopen(request, timeout=110) as response:
            raw = json.load(response)
    if raw.get("remark"):
        raise RuntimeError(
            raw["remark"]
        )  # Never replace a snapshot with partial results.
    records = []
    for element in raw["elements"]:
        tags = element.get("tags", {})
        center = element.get("center", element)
        lat, lng = center.get("lat"), center.get("lon")
        if lat is None or lng is None:
            raise ValueError(f'Missing coordinates: {element["id"]}')
        zone = get_tz(lng, lat)
        if not zone:
            raise ValueError(f'Missing timezone: {element["id"]}')
        record = {
            "id": f'{element["type"]}/{element["id"]}',
            "name": tags.get("name:en") or tags.get("name") or "Unnamed masjid",
            "lat": lat,
            "lng": lng,
            "zone": zone,
            "country": country,
            "address": tags.get("addr:full")
            or " ".join(
                filter(
                    None,
                    [
                        tags.get("addr:housenumber"),
                        tags.get("addr:street"),
                    ],
                )
            ),
            "city": tags.get("addr:city", ""),
            "region": tags.get("addr:state", ""),
        }
        for target, keys in {
            "website": ["website", "contact:website", "url"],
            "phone": ["phone", "contact:phone"],
            "serviceTimes": ["service_times"],
            "openingHours": ["opening_hours"],
            "wheelchair": ["wheelchair"],
        }.items():
            value = next((tags[k] for k in keys if tags.get(k)), None)
            if value:
                record[target] = value
        records.append(record)
    if not records:
        raise ValueError("Empty import; preserving the existing snapshot")
    result = {
        "schemaVersion": 1,
        "country": country,
        "importedAt": raw["osm3s"]["timestamp_osm_base"],
        "source": "https://www.openstreetmap.org/copyright",
        "license": "ODbL-1.0",
        "query": "amenity=place_of_worship AND religion=muslim",
        "masjids": sorted(records, key=lambda m: m["id"]),
    }
    output = args.output or Path(f"assets/data/masjids/{country.lower()}.json")
    if output.exists():
        previous = json.loads(output.read_text())
        if len(records) < len(previous["masjids"]) * 0.8:
            raise ValueError("Unexpected OSM shrink; previous snapshot preserved")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    temp.replace(output)
    print(
        f'{len(records)} masjids written to {output}; snapshot {result["importedAt"]}'
    )


if __name__ == "__main__":
    main()
