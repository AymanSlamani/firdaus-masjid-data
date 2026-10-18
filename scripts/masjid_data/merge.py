"""Conservative matching, stable IDs, and a source-by-source coverage audit."""

from __future__ import annotations
from collections import Counter
from copy import deepcopy
import datetime as dt
from difflib import SequenceMatcher
import math
import re
import unicodedata
import urllib.parse
from zoneinfo import ZoneInfo
from .sources import STATE_CODES, clean


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).lower()
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(
        r"\b(street|avenue|road|boulevard|drive|highway|lane|court|suite)\b",
        lambda m: {
            "street": "st",
            "avenue": "ave",
            "road": "rd",
            "boulevard": "blvd",
            "drive": "dr",
            "highway": "hwy",
            "lane": "ln",
            "court": "ct",
            "suite": "ste",
        }[m[0]],
        value,
    )
    return " ".join(re.findall(r"\w+", value))


def distance(a, b):
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    return math.hypot(
        (a["lat"] - b["lat"]) * 111195, (a["lng"] - b["lng"]) * 111195 * math.cos(lat)
    )


def category_review(row, exclusions=None):
    if row["id"] in (exclusions or {}):
        return {
            "id": row["id"],
            "name": row["name"],
            "reason": exclusions[row["id"]],
            "sourceUrl": row.get("sourceUrl"),
        }
    name = normalize(row["name"])
    explicit_prayer_space = re.search(
        r"\b(masjid|masajid|mosque|musallah?|prayer|center|centre)\b", name
    )
    if not explicit_prayer_space and re.search(r"\b(church|cemetery)\b", name):
        return {
            "id": row["id"],
            "name": row["name"],
            "reason": "Directory category needs review: name identifies a church or cemetery without an explicit mosque/prayer-space designation",
            "sourceUrl": row.get("sourceUrl"),
        }
    return None


def match(records, incoming):
    best, score = None, 0
    target_name = normalize(incoming["name"])
    target_address = normalize(incoming.get("address", ""))
    target_phone = re.sub(r"\D", "", incoming.get("phone") or "")[-10:]
    for existing in records:
        if (
            abs(existing["lat"] - incoming["lat"]) > 0.008
            or abs(existing["lng"] - incoming["lng"]) > 0.015
        ):
            continue
        meters = distance(existing, incoming)
        if meters > 600:
            continue
        names = [existing["name"], *existing.get("aliases", [])]
        similarity = max(
            SequenceMatcher(None, normalize(n), target_name).ratio() for n in names
        )
        same_address = bool(
            target_address and target_address == normalize(existing.get("address", ""))
        )
        phone = re.sub(r"\D", "", existing.get("phone") or "")[-10:]
        same_phone = bool(target_phone and phone == target_phone)
        confidence = 0
        existing_host = (
            urllib.parse.urlsplit(existing.get("website") or "").hostname or ""
        ).removeprefix("www.")
        incoming_host = (
            urllib.parse.urlsplit(incoming.get("website") or "").hostname or ""
        ).removeprefix("www.")
        if meters < 150 and existing_host and existing_host == incoming_host:
            confidence = 0.97
        if meters < 120 and similarity >= 0.76:
            confidence = 0.8 + similarity / 10
        if meters < 350 and similarity >= 0.94:
            confidence = 0.9 + similarity / 20
        if meters < 500 and same_address and (similarity >= 0.43 or same_phone):
            confidence = 0.94
        if meters < 200 and same_phone and similarity >= 0.4:
            confidence = 0.96
        if confidence > score:
            best, score = existing, confidence
    return best, score


def build_catalog(
    osm: dict,
    usa: dict,
    masjidal: dict,
    *,
    timezone_for,
    schedules=None,
    state_for=None,
    extra_sources=None,
    discovery=None,
    exclusions=None,
):
    records, merges, coordinate_review = [], [], []
    for raw in osm["masjids"]:
        row = deepcopy(raw)
        row["sourceUrl"] = "https://www.openstreetmap.org/" + row["id"]
        review = category_review(row, exclusions)
        if review:
            coordinate_review.append(review)
            continue
        row["sources"] = [
            {"provider": "openstreetmap", "id": row["id"], "url": row["sourceUrl"]}
        ]
        row["region"] = STATE_CODES.get(row.get("region"), row.get("region", ""))
        row.setdefault("providers", {})
        row.setdefault("aliases", [])
        records.append(row)
    input_counts = {
        "openstreetmap": len(osm["masjids"]),
        "masjidsinusa": len(usa["masjids"]),
        "masjidal": len(masjidal["masjids"]),
    }
    for extra in extra_sources or []:
        input_counts[extra.get("sourceName", "extra")] = len(extra["masjids"])
    for payload in [usa, masjidal, *(extra_sources or [])]:
        for raw in payload["masjids"]:
            incoming = deepcopy(raw)
            review = category_review(incoming, exclusions)
            if review:
                coordinate_review.append(review)
                continue
            zone = timezone_for(incoming["lng"], incoming["lat"])
            us_zones = {
                "America/New_York",
                "America/Chicago",
                "America/Denver",
                "America/Los_Angeles",
                "America/Detroit",
                "America/Boise",
                "America/Phoenix",
                "America/Anchorage",
                "America/Juneau",
                "America/Metlakatla",
                "America/Nome",
                "America/Sitka",
                "America/Yakutat",
                "America/Adak",
                "America/Menominee",
                "Pacific/Honolulu",
                "America/Puerto_Rico",
                "America/St_Thomas",
                "Pacific/Guam",
                "Pacific/Pago_Pago",
                "Pacific/Saipan",
            }
            if zone not in us_zones and not zone.startswith(
                ("America/Indiana/", "America/Kentucky/", "America/North_Dakota/")
            ):
                coordinate_review.append(
                    {
                        "id": incoming["id"],
                        "name": incoming["name"],
                        "lat": incoming["lat"],
                        "lng": incoming["lng"],
                        "reason": "US record has coordinates outside US timezones",
                        "sourceUrl": incoming.get("sourceUrl"),
                    }
                )
                continue
            existing, confidence = match(records, incoming)
            if existing is None:
                incoming["zone"] = timezone_for(incoming["lng"], incoming["lat"])
                incoming.setdefault("providers", {})
                incoming.setdefault("aliases", [])
                records.append(incoming)
                continue
            merges.append(
                {
                    "kept": existing["id"],
                    "merged": incoming["id"],
                    "confidence": round(confidence, 3),
                }
            )
            if (
                incoming["name"] != existing["name"]
                and incoming["name"] not in existing["aliases"]
            ):
                existing["aliases"].append(incoming["name"])
            existing["sources"].extend(incoming["sources"])
            for key, value in incoming.get("providers", {}).items():
                prior = existing["providers"].get(key)
                if prior and prior != value:
                    # Multiple provider accounts at one address need explicit review.
                    existing.setdefault("providerAlternates", {}).setdefault(
                        key, []
                    ).append(value)
                else:
                    existing["providers"][key] = value
            for key in ["address", "city", "region", "postalCode", "website", "phone"]:
                if not clean(existing.get(key)) and clean(incoming.get(key)):
                    existing[key] = incoming[key]
    found = {r["id"]: r for r in (discovery or {}).get("results", [])}
    for row in records:
        if row["id"] in found:
            item = found[row["id"]]
            if item.get("providers"):
                row["providers"].update(item["providers"])
                row["providerEvidenceUrl"] = item.get("evidenceUrl")
            if item.get("timetableUrl"):
                row["timetableUrl"] = item["timetableUrl"]
        row["region"] = STATE_CODES.get(
            row.get("region"), row.get("region", "")
        ).upper()
        if row["region"] not in STATE_CODES.values() and state_for:
            row["region"] = state_for(row["lng"], row["lat"]) or row["region"]
        if not row.get("zone"):
            raise ValueError("Missing timezone for " + row["id"])
        row.pop("shellAccount", None)
        provider_id = row.get("providers", {}).get("masjidal")
        if schedules and row.get("providers", {}).get("mohid") in schedules:
            row["publishedSchedule"] = schedules[row["providers"]["mohid"]]
        elif schedules and provider_id in schedules:
            row["publishedSchedule"] = schedules[provider_id]
        elif schedules and row.get("providers", {}).get("prayersconnect"):
            key = "prayersconnect/" + row["providers"]["prayersconnect"]
            if key in schedules:
                row["publishedSchedule"] = schedules[key]
    records.sort(key=lambda m: m["id"])
    regions = Counter(row.get("region") or "unknown" for row in records)
    linked = sum(bool(row.get("providers")) for row in records)
    current = []
    now = dt.datetime.now(dt.timezone.utc)
    for row in records:
        schedule = row.get("publishedSchedule")
        if (
            schedule
            and schedule["date"]
            == now.astimezone(ZoneInfo(row["zone"])).date().isoformat()
        ):
            current.append(row)
    report = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputRecords": input_counts,
        "sourceSnapshots": {
            "openstreetmap": osm.get("importedAt"),
            "masjidsinusa": usa.get("importedAt"),
            "masjidal": masjidal.get("importedAt"),
            **{
                extra.get("sourceName", "extra"): extra.get("importedAt")
                for extra in extra_sources or []
            },
        },
        "uniqueLocations": len(records),
        "mergedRecords": len(merges),
        "merges": merges,
        "rejectedOrReview": [
            *usa.get("rejected", []),
            *coordinate_review,
            *(
                item
                for extra in extra_sources or []
                for item in extra.get("rejected", [])
            ),
        ],
        "byState": dict(sorted(regions.items())),
        "withWebsite": sum(bool(row.get("website")) for row in records),
        "withPhone": sum(bool(row.get("phone")) for row in records),
        "withLiveProvider": linked,
        "withPublishedSnapshot": sum(
            bool(row.get("publishedSchedule")) for row in records
        ),
        "withCurrentPublishedSchedule": len(current),
        "withCurrentJumuah": sum(
            bool(r["publishedSchedule"].get("jumuah")) for r in current
        ),
        "withFiveDailyIqamah": sum(
            len(r["publishedSchedule"].get("iqamah", {})) == 5 for r in current
        ),
        "withoutLiveProvider": len(records) - linked,
        "providerAccountConflicts": [
            {"id": r["id"], "accounts": r["providerAlternates"]}
            for r in records
            if r.get("providerAlternates")
        ],
        "completeCensus": False,
        "limits": [
            "Source union, not a certified exhaustive census.",
            "A provider binding does not guarantee a complete or current timetable.",
            "Unpublished congregation times remain unknown.",
        ],
    }
    return {
        "schemaVersion": 2,
        "country": "US",
        "importedAt": report["generatedAt"],
        "source": "Multiple attributed public directory and timetable sources",
        "license": "See NOTICE.txt; OpenStreetMap-derived records are ODbL-1.0",
        "coverage": {
            k: v
            for k, v in report.items()
            if k not in {"merges", "rejectedOrReview", "providerAccountConflicts"}
        },
        "masjids": records,
    }, report
