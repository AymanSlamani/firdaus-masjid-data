#!/usr/bin/env python3
"""Inspect public official sites for supported embedded timetable providers.

Robots rules are respected. Only same-origin prayer-page links are followed.
No credentials, accounts, or personal contact messages are used.
"""

from collections import Counter, defaultdict
from masjid_data.merge import distance
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import argparse
import hashlib
import html
import ipaddress
import json
from pathlib import Path
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

CACHE = Path(".tools/masjid-data/website-cache")
CACHE.mkdir(parents=True, exist_ok=True)
UA = "Firdaus/1.0 (public mosque timetable discovery)"


def public_url(value):
    uri = urllib.parse.urlsplit(value)
    if (
        uri.scheme not in {"https", "http"}
        or not uri.hostname
        or uri.username
        or uri.password
    ):
        raise ValueError("Invalid public URL")
    if uri.port not in {None, 80, 443}:
        raise ValueError("Unexpected website port")
    addresses = socket.getaddrinfo(uri.hostname, None)
    if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Private/local network URL refused")
    return value


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def get(url):
    public_url(url)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = CACHE / key
    if path.exists() and time.time() - path.stat().st_mtime < 604800:
        return path.read_text()
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=12
    ) as response:
        if "text/" not in response.headers.get(
            "Content-Type", ""
        ) and "json" not in response.headers.get("Content-Type", ""):
            raise ValueError("Not a text page")
        data = response.read(3_000_001)
        if len(data) > 3_000_000:
            raise ValueError("Page too large")
        value = data.decode("utf-8", errors="replace")
    path.write_text(value)
    return value


def origin(url):
    u = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "", "", ""))


def host(url):
    return (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.").lower()


def candidates(page):
    urls = []
    for value in re.findall(
        r'(?:https?:)?//[^\s"\x27<>\\]+', html.unescape(page.replace("\\/", "/"))
    ):
        if value.startswith("//"):
            value = "https:" + value
        try:
            uri = urllib.parse.urlsplit(value)
        except ValueError:
            continue
        provider_host = (uri.hostname or "").lower()
        if (
            provider_host in {"us.mohid.co", "mohid.co"}
            and uri.path.endswith("/widget/api/index/")
            and urllib.parse.parse_qs(uri.query).get("m") == ["prayertimings"]
        ):
            value = urllib.parse.urlunsplit(
                ("https", uri.netloc, uri.path, "m=prayertimings", "")
            )
            urls.append(("mohid", value))
        elif provider_host in {"masjidal.com", "www.masjidal.com"}:
            identity = urllib.parse.parse_qs(uri.query).get("masjid_id", [""])[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{4,64}", identity):
                urls.append(("masjidal", identity))
        elif provider_host in {"mawaqit.net", "www.mawaqit.net"} and uri.path not in {
            "/",
            "/en",
            "/fr",
        }:
            urls.append(("mawaqit", value))
    return sorted(set(urls))


def main():
    global CACHE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".tools/masjid-data"))
    parser.add_argument(
        "--catalog", type=Path, default=Path("assets/data/masjids/us.json")
    )
    args = parser.parse_args()
    CACHE = args.work_dir / "website-cache"
    CACHE.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(args.catalog.read_text())["masjids"]
    owners = defaultdict(list)
    for m in catalog:
        if m.get("website"):
            owners[host(m["website"])].append(m)
    prior_path = args.work_dir / "discovery.json"
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    previous = {r["id"]: r for r in prior.get("results", []) + prior.get("errors", [])}
    rows = [
        m
        for m in catalog
        if m.get("website") and (m["id"] in previous or not m.get("publishedSchedule"))
    ]
    stop = threading.Event()
    results, errors = [], []

    def one(m):
        if stop.is_set():
            return None
        saved = previous.get(m["id"])
        if saved and saved.get("error") and saved["error"] != "Invalid IPv6 URL":
            checked = saved.get("checkedAt", prior.get("generatedAt"))
            if checked and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                checked
            ) < dt.timedelta(days=7):
                return {**saved, "checkedAt": checked}
        website = m["website"]
        if "://" not in website:
            website = "https://" + website
        u = urllib.parse.urlsplit(website)
        # Ignore social profiles: they are not a mosque-controlled timetable endpoint.
        if any(
            host(website).endswith(s)
            for s in [
                "facebook.com",
                "instagram.com",
                "youtube.com",
                "twitter.com",
                "x.com",
            ]
        ):
            return {"id": m["id"], "skipped": "social-profile"}
        base = origin(website)
        try:
            robot = urllib.robotparser.RobotFileParser()
            try:
                robot.parse(get(base + "/robots.txt").splitlines())
            except urllib.error.HTTPError as e:
                if e.code not in {404, 410}:
                    raise
                robot.parse([])
            if not robot.can_fetch(UA, website):
                return {"id": m["id"], "skipped": "robots-disallowed"}
            page = get(website)
            found = candidates(page)
            evidence = website
            timetable = None
            if not found:
                links = []
                for link in re.findall(r'href=["\x27]([^"\x27]+)', page, re.I):
                    link = urllib.parse.urljoin(website, html.unescape(link))
                    if (
                        host(link) == host(website)
                        and re.search(
                            r"prayer|salah|salat|jummah|jumuah|namaz",
                            urllib.parse.urlsplit(link).path,
                            re.I,
                        )
                        and not re.search(r"\.(pdf|png|jpg|jpeg)$", link, re.I)
                    ):
                        links.append(link)
                for link in list(dict.fromkeys(links))[:2]:
                    if not robot.can_fetch(UA, link):
                        continue
                    timetable = link
                    time.sleep(0.3)
                    found = candidates(get(link))
                    if found:
                        evidence = link
                        break
            bindings = {}
            reason = None
            if any(distance(m, other) > 150 for other in owners[host(website)]):
                reason = "shared-site-multiple-locations"
            for provider in ["mohid", "masjidal"]:
                matches = [value for key, value in found if key == provider]
                if len(matches) == 1 and reason is None:
                    bindings[provider] = matches[0]
            return {
                "id": m["id"],
                "website": website,
                "evidenceUrl": evidence,
                "timetableUrl": timetable,
                "providers": bindings,
                "candidates": [{"provider": k, "value": v} for k, v in found],
                "reviewReason": reason,
            }
        except urllib.error.HTTPError as e:
            if e.code == 429:
                stop.set()
            return {"id": m["id"], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"id": m["id"], "error": str(e)[:200]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, m) for m in rows]
        for i, future in enumerate(as_completed(futures), 1):
            value = future.result()
            if value:
                value.setdefault(
                    "checkedAt", dt.datetime.now(dt.timezone.utc).isoformat()
                )
                saved = previous.get(value["id"])
                if value.get("error") and saved and saved.get("providers"):
                    mosque = next(m for m in rows if m["id"] == value["id"])
                    if saved.get("website") == mosque.get("website"):
                        value = {
                            **saved,
                            "lastDiscoveryError": value["error"],
                            "checkedAt": value["checkedAt"],
                        }
                if value.get("error"):
                    errors.append(value)
                else:
                    results.append(value)
            if i % 25 == 0:
                print(
                    f'Website discovery {i}/{len(rows)}: {sum(bool(r.get("providers")) for r in results)} unambiguous widget mappings; {len(errors)} unavailable',
                    flush=True,
                )
    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "results": results,
        "errors": errors,
        "complete": not stop.is_set(),
    }
    if stop.is_set():
        (args.work_dir / "discovery-incomplete.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n"
        )
        raise RuntimeError(
            "Website rate limit; previous complete discovery snapshot preserved"
        )
    path = args.work_dir / "discovery.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)
    print(
        f'Website discovery finished: {sum(bool(r.get("providers")) for r in results)} mappings',
        flush=True,
    )


if __name__ == "__main__":
    main()
