# Firdaus U.S. mosque data feed

An attributed, reproducible public directory and dated congregation schedule
feed for Firdaus. The app's source code, user preferences, bookmarks, personal
notes, and recordings are not part of this repository.

- [Catalog](catalog/us.json): schema v2, locations, stable source IDs, IANA zones,
  source links, supported public provider bindings, and dated timetable snapshots.
- [Status](catalog/status.json): latest completed refresh and coverage counts.
- [Coverage audit](catalog/coverage.json): counts by state, merges, quarantined
  records, and ambiguous provider accounts.
- [Schedule gaps](catalog/schedule-gaps.json): locations needing a public schedule
  source or current provider data.

The feed is a union of available public sources, **not a certified census of every
U.S. mosque**. A listed mosque does not necessarily publish iqamah or Jumu'ah
online. Missing times are never generated from calculated prayer starts. Provider
publication, retrieval time, personal edits, and independent verification are
different concepts. No blanket "verified" flag is inferred from inclusion.

## Refreshing

The Actions workflow refreshes at 12:17 UTC daily. It downloads full public
source pagination, retrieves dated Masjidal and PrayersConnect schedules, refreshes unambiguous
MOHID widgets linked from mosque websites, validates the entire result, and
commits only a completed snapshot. Sunday scheduled runs additionally refresh
OpenStreetMap, eSalah and PrayersConnect directories, and website discovery. Manual workflow runs
can request the full refresh. Runs can be delayed by GitHub or upstream outages;
consumers must inspect dates and keep an offline fallback. Optional weekly
source failures retain the last complete input for that source and appear in
`status.json.refreshWarnings`; they do not block fresh dated schedules from
other providers. Directory snapshot dates are in `coverage.json.sourceSnapshots`.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run-masjid-feed-refresh.py
# Complete directory / discovery refresh:
.venv/bin/python scripts/run-masjid-feed-refresh.py --full
```

HTTP caches are local/Actions-cache only; raw crawled pages are not published.
Directory requests are cached, the eSalah adapter stays below 60 requests/minute,
provider requests are throttled, 429 stops the run, and failed or partial imports
do not overwrite the last complete publication. Homepage discovery respects
robots rules and does not bypass certificate failures, authentication, or 403s.
Distinct branches sharing a website require review before automatic widget binding.

For feeds consumed by an application:

```text
https://raw.githubusercontent.com/AymanSlamani/firdaus-masjid-data/main/catalog/us.json
```

The app keeps a validated disk copy, checks for catalog updates every six hours,
and refreshes supported mosque timetables on demand with a thirty-minute local
cache. Dated cached congregation times expire when the mosque's local date changes.
The astronomy calculation remains independent of congregation times.

## Source boundaries

- OpenStreetMap contributors: Muslim places of worship inside the U.S. area;
  ODbL attribution and original element URLs are retained.
- MasjidsInUSA: public factual directory fields; records explicitly categorized
  as other religions, foreign locations, or insufficiently identified are excluded
  or queued for review. Ratings, photos, reviews, and generated descriptions are
  not copied.
- Masjidal: public account directory and public dated timetable response. Shell
  accounts are not counted as successful congregation schedule imports. The v1
  timetable is used for sessions exactly present in that response; no extra
  Friday session is supplied as a default.
- MOHID: only public mosque-embedded widgets with an unambiguous source mapping.
  Khutbah and salah are separate fields. A published "After N min" rule is applied
  to the widget's own start time. Widgets lacking an explicit date are labeled
  by retrieval date in the mosque's timezone and expire that day; retrieval does
  not prove that the mosque recently edited its timetable.
- eSalah: its documented anonymous directory API is used for additional public
  profiles, honoring the public rate limit.
- PrayersConnect: documented anonymous guest API; guest tokens stay in memory.
  Source-backed daily iqamah records are retained; calculated `waqt` fallbacks
  and substituted Dhuhr Friday salah are excluded. Friday khutbah and salah
  remain separate. No API-generated astronomical start is called mosque-published.
- Church-only and cemetery-only names without an explicit mosque/prayer-space
  designation are held in the category review queue, rather than counted as
  regular congregation venues.
- US Census state cartographic boundaries assign missing state codes; tzfpy
  resolves IANA timezones from geographic polygons.

Masjidi and MAWAQIT integration access is not implied by a public website. Their
production APIs require provider authorization. No demo key or website-exposed
credential is used as production access here. A private or unpublished schedule
remains a gap until its mosque/provider publishes it or grants permission.

See [NOTICE.txt](NOTICE.txt) for attribution and data licensing.

## Corrections

Use the [missing mosque](https://github.com/AymanSlamani/firdaus-masjid-data/issues/new?template=missing-mosque.yml) or
[schedule correction](https://github.com/AymanSlamani/firdaus-masjid-data/issues/new?template=schedule.yml) issue forms with a
public source. Submissions require human review before entering the feed. Do not
submit personal contact information, private notes, or private schedules.

`inputs/review-overrides.json` holds explicit source-record exclusions pending
public-venue confirmation. Each exclusion remains visible in the coverage audit;
other sources for the same location are preserved.
