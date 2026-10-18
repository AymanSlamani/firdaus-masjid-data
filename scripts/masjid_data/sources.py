"""Source adapters. Preserve public facts and dates; never infer iqamah times."""

from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Firdaus/1.0 (public mosque directory; cached imports)"
STATE_CODES = dict(
    zip(
        [
            "Alabama",
            "Alaska",
            "Arizona",
            "Arkansas",
            "California",
            "Colorado",
            "Connecticut",
            "Delaware",
            "District of Columbia",
            "Florida",
            "Georgia",
            "Hawaii",
            "Idaho",
            "Illinois",
            "Indiana",
            "Iowa",
            "Kansas",
            "Kentucky",
            "Louisiana",
            "Maine",
            "Maryland",
            "Massachusetts",
            "Michigan",
            "Minnesota",
            "Mississippi",
            "Missouri",
            "Montana",
            "Nebraska",
            "Nevada",
            "New Hampshire",
            "New Jersey",
            "New Mexico",
            "New York",
            "North Carolina",
            "North Dakota",
            "Ohio",
            "Oklahoma",
            "Oregon",
            "Pennsylvania",
            "Rhode Island",
            "South Carolina",
            "South Dakota",
            "Tennessee",
            "Texas",
            "Utah",
            "Vermont",
            "Virginia",
            "Washington",
            "West Virginia",
            "Wisconsin",
            "Wyoming",
            "Puerto Rico",
        ],
        [
            "AL",
            "AK",
            "AZ",
            "AR",
            "CA",
            "CO",
            "CT",
            "DE",
            "DC",
            "FL",
            "GA",
            "HI",
            "ID",
            "IL",
            "IN",
            "IA",
            "KS",
            "KY",
            "LA",
            "ME",
            "MD",
            "MA",
            "MI",
            "MN",
            "MS",
            "MO",
            "MT",
            "NE",
            "NV",
            "NH",
            "NJ",
            "NM",
            "NY",
            "NC",
            "ND",
            "OH",
            "OK",
            "OR",
            "PA",
            "RI",
            "SC",
            "SD",
            "TN",
            "TX",
            "UT",
            "VT",
            "VA",
            "WA",
            "WV",
            "WI",
            "WY",
            "PR",
        ],
    )
)
# Explicit assignments avoid accidental zip alignment when editing the table.
STATE_CODES.update(
    {
        "Virginia": "VA",
        "Vermont": "VT",
        "Washington": "WA",
        "West Virginia": "WV",
        "Wisconsin": "WI",
        "Wyoming": "WY",
        "Puerto Rico": "PR",
    }
)


class Fetcher:
    def __init__(self, cache: Path, delay: float = 0.6, max_age: int = 86400):
        self.cache, self.delay, self.max_age = cache, delay, max_age
        cache.mkdir(parents=True, exist_ok=True)
        self.last_request = 0.0
        self.requests = 0
        self.lock = threading.Lock()

    def get(self, url: str, *, fresh: bool = False) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / key
        if (
            path.exists()
            and not fresh
            and time.time() - path.stat().st_mtime < self.max_age
        ):
            return path.read_text()
        with self.lock:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/html;q=.9,*/*;q=.5",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(12_000_001)
                if len(data) > 12_000_000:
                    raise ValueError("Source response too large")
                text = data.decode("utf-8")
                self.requests += 1
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise RuntimeError(
                    f"Rate limited by {urllib.parse.urlsplit(url).netloc}; stop and retry later"
                ) from error
            raise
        temp = path.with_suffix(".tmp")
        temp.write_text(text)
        temp.replace(path)
        return text

    def retrieved_at(self, url: str) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()
        return dt.datetime.fromtimestamp(
            (self.cache / key).stat().st_mtime, dt.timezone.utc
        ).isoformat()

    def json(self, url: str):
        return json.loads(self.get(url))


def clean(value):
    return (
        value.strip()
        if isinstance(value, str) and value.strip() not in {"", "-", "N/A", "null"}
        else None
    )


def flight_records(html: str) -> list[dict]:
    """Read public server-rendered mosque props without executing page scripts."""
    chunks = []
    for raw in re.findall(r"self\.__next_f\.push\((\[.*?\])\)</script>", html):
        try:
            item = json.loads(raw)
            if len(item) > 1 and isinstance(item[1], str):
                chunks.append(item[1])
        except (ValueError, TypeError):
            continue
    stream = "".join(chunks)
    decoder, records = json.JSONDecoder(), []
    for match in re.finditer(r'"mosque":\s*(\{)', stream):
        try:
            record, _ = decoder.raw_decode(stream, match.start(1))
            records.append(record)
        except ValueError:
            continue
    return records


def import_usa_directory(fetch: Fetcher) -> tuple[list[dict], list[dict]]:
    base = "https://masjidsinusa.com"
    home = fetch.get(base + "/")
    states = sorted(set(re.findall(r'href="(/states/[a-z-]+/)"', home)))
    allowed = {
        "/states/" + name.lower().replace(" ", "-") + "/" for name in STATE_CODES
    }
    states = [state for state in states if state in allowed]
    if len(states) < 40:
        raise ValueError(
            f"Incomplete state index ({len(states)}); refusing partial import"
        )
    records, rejected, seen = [], [], set()
    for index, state in enumerate(states):
        page = fetch.get(base + state)
        rows = flight_records(page)
        if not rows:
            raise ValueError("No source records on " + state)
        for row in rows:
            identity = (
                row.get("place_id")
                or hashlib.sha256(
                    f'{row.get("title")}/{row.get("address")}'.encode()
                ).hexdigest()[:20]
            )
            if identity in seen:
                continue
            seen.add(identity)
            name = clean(row.get("title")) or ""
            categories = row.get("category_ids") or []
            country = row.get("country_code")
            reason = None
            if country != "US":
                reason = "outside-us"
            elif row.get("region") not in STATE_CODES:
                reason = "unrecognized-us-region"
            elif re.search(
                r"\b(hindu|baps|swaminarayan|mandir|synagogue|church|shriners?|masonic)\b",
                name,
                re.I,
            ):
                reason = "non-mosque-place"
            elif "mosque" not in categories and not re.search(
                r"masj[iie]d|mosque|muslim|islam|jamatkhana|jamaat|musalla|jamia|darul|muhammad|مسجد|tarbiya",
                name,
                re.I,
            ):
                reason = "needs-mosque-review"
            lat, lng = row.get("latitude"), row.get("longitude")
            if (
                not isinstance(lat, (int, float))
                or not isinstance(lng, (int, float))
                or not (-90 <= lat <= 90 and -180 <= lng <= 180)
            ):
                reason = "missing-coordinates"
            if reason:
                rejected.append(
                    {
                        "id": identity,
                        "name": name,
                        "reason": reason,
                        "sourceUrl": base + state,
                    }
                )
                continue
            records.append(
                {
                    "id": "usa/" + identity,
                    "name": name,
                    "address": clean(row.get("street_address")) or "",
                    "city": clean(row.get("city")) or "",
                    "region": STATE_CODES[row["region"]],
                    "country": "US",
                    "postalCode": clean(row.get("zip_code")) or "",
                    "lat": lat,
                    "lng": lng,
                    "website": clean(row.get("url")),
                    "phone": clean(row.get("phone")),
                    "sourceUrl": base + state,
                    "sources": [
                        {
                            "provider": "masjidsinusa",
                            "id": identity,
                            "url": base + state,
                        }
                    ],
                }
            )
        print(
            f"US directory {index+1}/{len(states)}: {state}, {len(rows)} source records",
            flush=True,
        )
    return records, rejected


def import_masjidal_directory(fetch: Fetcher) -> list[dict]:
    records = []
    page = 1
    expected = None
    processed = 0
    while True:
        query = urllib.parse.urlencode({"per-page": 50, "page": page})
        payload = fetch.json("https://masjidal.com/api/v3/masjids?" + query)
        meta = payload["_meta"]
        expected = meta["totalCount"] if expected is None else expected
        items = payload["items"]
        if not items and processed < expected:
            raise ValueError("Masjidal returned a partial directory")
        for row in items:
            if row.get("country") != "US":
                continue
            try:
                lat, lng = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            if (
                not math.isfinite(lat)
                or not math.isfinite(lng)
                or not (-90 <= lat <= 90 and -180 <= lng <= 180)
            ):
                continue
            identity = str(row["id"])
            records.append(
                {
                    "id": "masjidal/" + identity,
                    "name": row["name"].strip(),
                    "address": clean(row.get("address")) or "",
                    "city": clean(row.get("city")) or "",
                    "region": clean(row.get("state")) or "",
                    "country": "US",
                    "postalCode": clean(row.get("zipcode")) or "",
                    "lat": lat,
                    "lng": lng,
                    "website": clean(row.get("website_url")),
                    "phone": clean(row.get("phone")),
                    "sourceUrl": f"https://masjidal.com/widget/monthly/v2/?masjid_id={identity}",
                    "sources": [
                        {
                            "provider": "masjidal",
                            "id": identity,
                            "url": f"https://masjidal.com/widget/monthly/v2/?masjid_id={identity}",
                        }
                    ],
                    "providers": {"masjidal": identity},
                    "shellAccount": row.get("is_shell_account") is True,
                }
            )
        processed += len(items)
        print(
            f'Masjidal directory {page}/{meta["pageCount"]}: {len(records)} US records',
            flush=True,
        )
        if page >= meta["pageCount"]:
            break
        page += 1
    if processed < expected:
        raise ValueError("Incomplete Masjidal import")
    return records
