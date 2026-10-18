"""Documented public guest API; tokens stay in memory and out of publication."""

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo
from .sources import USER_AGENT

ENDPOINT = "https://api-v2.prayersconnect.com/graphql"
PRAYERS = {
    "fajr": "Fajr",
    "dhuhr": "Dhuhr",
    "asr": "Asr",
    "maghrib": "Maghrib",
    "isha": "Isha",
}
DETAIL_FIELDS = (
    "name timezone prayer_times{"
    + " ".join(p + "{type iqamah meta{type_source}}" for p in PRAYERS)
    + "}"
)
FRIDAY_FIELDS = "dhuhr " + " ".join(
    "jummah_" + field + "_" + str(n)
    for n in range(1, 4)
    for field in ["khutba", "salah"]
)


class GuestClient:
    def __init__(self, cache, max_age=21600):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.max_age = max_age
        self.token = None
        self.last_request = 0

    def _post(self, body, auth=True):
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if auth:
            if not self.token:
                self.token = self._post(
                    {"query": "mutation { registerGuest { access_token } }"}, False
                )["data"]["registerGuest"]["access_token"]
            headers["Authorization"] = "Bearer " + self.token
        time.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = urllib.request.Request(
            ENDPOINT, data=json.dumps(body).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(12000001)
                if len(raw) > 12000000:
                    raise ValueError("Provider response exceeds limit")
                data = json.loads(raw)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise RuntimeError(
                    "Provider rate limit: stop and preserve previous snapshot"
                ) from error
            raise
        if data.get("errors"):
            raise ValueError(
                "Public provider query failed: "
                + "; ".join(e.get("message", "") for e in data["errors"])
            )
        return data

    def query(self, query, variables=None):
        body = {"query": query, "variables": variables or {}}
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        path = self.cache / (key + ".json")
        if path.exists() and time.time() - path.stat().st_mtime < self.max_age:
            data = json.loads(path.read_text())
        else:
            for attempt in range(3):
                try:
                    data = self._post(body)
                    break
                except urllib.error.HTTPError as error:
                    if error.code not in {502, 503, 504} or attempt == 2:
                        raise
                    time.sleep(2 ** (attempt + 1))
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        retrieved = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        return data["data"], retrieved


def minutes(value, zone, date):
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"\d{2}:\d{2}", value):
        h, m = map(int, value.split(":"))
        return h * 60 + m if h < 24 and m < 60 else None
    try:
        instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return None
        local = instant.astimezone(ZoneInfo(zone))
        return (
            local.hour * 60 + local.minute if local.date().isoformat() == date else None
        )
    except (ValueError, TypeError):
        return None


def parse_schedule(detail, flat, identity, date, retrieved, zone):
    iqamah, sessions, issues = {}, [], []
    for field, label in PRAYERS.items():
        prayer = (detail.get("prayer_times") or {}).get(field) or {}
        # The API also supplies calculated waqt as a fallback iqamah. Only a
        # source-backed record is a congregation time, regardless of its flag.
        if prayer.get("type") != "record" or not (prayer.get("meta") or {}).get(
            "type_source"
        ):
            continue
        value = minutes(prayer.get("iqamah"), zone, date)
        if value is not None:
            iqamah[label] = value
    flat = flat or {}
    dhuhr = minutes(flat.get("dhuhr"), zone, date)
    if "Fajr" in iqamah and dhuhr is not None and iqamah["Fajr"] >= dhuhr:
        del iqamah["Fajr"]
        issues.append("Fajr iqamah is after Dhuhr in the source and was omitted.")
    for n in range(1, 4):
        khutbah = minutes(flat.get(f"jummah_khutba_{n}"), zone, date)
        salah = minutes(flat.get(f"jummah_salah_{n}"), zone, date)
        if salah == dhuhr:
            salah = None  # Provider substitutes calculated Dhuhr for missing salah.
        if khutbah == 0:
            khutbah = None
        if salah == 0:
            salah = None
        session = {}
        if khutbah is not None:
            session["khutbah"] = khutbah
        if salah is not None:
            session["iqamah"] = salah
        if session:
            sessions.append(session)
    if not iqamah and not sessions:
        return None
    return {
        "provider": "prayersconnect",
        "providerId": str(identity),
        "sourceUrl": f"https://prayersconnect.com/mosques/{identity}",
        "date": date,
        "retrievedAt": retrieved.isoformat(),
        "iqamah": iqamah,
        "prayers": {},
        "fridaySessions": sessions,
        "jumuah": [s.get("khutbah", s.get("iqamah")) for s in sessions],
        "qualityIssues": issues,
    }
