import importlib.util
import datetime as dt
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Works both in the dedicated data repository and under the application scripts.
SCRIPTS = ROOT / "scripts" if (ROOT / "scripts").exists() else ROOT.parent
sys.path.insert(0, str(SCRIPTS))
from masjid_data.sources import flight_records, STATE_CODES
from masjid_data.merge import build_catalog
from masjid_data.prayersconnect import parse_schedule, minutes


def row(identity, name, lat=40.7128, lng=-74.006, website="https://example.org"):
    return {
        "id": identity,
        "name": name,
        "address": "100 Main St",
        "city": "New York",
        "region": "NY",
        "country": "US",
        "zone": "America/New_York",
        "lat": lat,
        "lng": lng,
        "website": website,
        "sources": [{"provider": "test", "id": identity, "url": "https://example.org"}],
    }


class ImportTests(unittest.TestCase):
    def test_prayersconnect_fallbacks_are_not_published_congregation_times(self):
        detail = {
            "prayer_times": {
                "fajr": {
                    "type": "record",
                    "iqamah": "2026-10-18T10:40:00Z",
                    "meta": {"type_source": "Mosques::IqamahPlan#1"},
                },
                "dhuhr": {
                    "type": "waqt",
                    "iqamah": "2026-10-18T16:44:00Z",
                    "meta": {"type_source": None},
                },
            }
        }
        result = parse_schedule(
            detail,
            {"dhuhr": "12:44", "jummah_salah_1": "12:44", "jummah_khutba_1": "13:00"},
            "1",
            "2026-10-18",
            dt.datetime.now(dt.timezone.utc),
            "America/New_York",
        )
        self.assertEqual(result["iqamah"], {"Fajr": 400})
        self.assertEqual(result["fridaySessions"], [{"khutbah": 780}])
        self.assertEqual(result["prayers"], {})
        self.assertIsNone(
            minutes("2026-10-19T10:40:00Z", "America/New_York", "2026-10-18")
        )
        self.assertEqual(
            minutes("2026-10-19T02:00:00Z", "America/New_York", "2026-10-18"), 1320
        )

    def test_public_flight_props_are_decoded_without_executing_javascript(self):
        record = {"title": "Masjid Example", "latitude": 40.7, "longitude": -74}
        data = "1:" + json.dumps({"mosque": record}) + "\n"
        page = "<script>self.__next_f.push(" + json.dumps([1, data]) + ")</script>"
        self.assertEqual(flight_records(page), [record])
        self.assertEqual(flight_records('<script>alert("no data")</script>'), [])

    def test_state_mapping_has_50_states_dc_and_puerto_rico(self):
        self.assertEqual(len(STATE_CODES), 52)
        self.assertEqual(len(set(STATE_CODES.values())), 52)
        self.assertEqual(STATE_CODES["Vermont"], "VT")
        self.assertEqual(STATE_CODES["Virginia"], "VA")
        self.assertEqual(STATE_CODES["Wyoming"], "WY")

    def test_merging_preserves_osm_identity_aliases_and_provider_bindings(self):
        osm = {"masjids": [row("node/1", "Example Islamic Center")]}
        usa = {"masjids": [row("usa/abc", "Example Masjid")], "rejected": []}
        provider = row("masjidal/1234", "Example Center")
        provider["providers"] = {"masjidal": "1234"}
        catalog, audit = build_catalog(
            osm,
            usa,
            {"masjids": [provider]},
            timezone_for=lambda *_: "America/New_York",
        )
        self.assertEqual(len(catalog["masjids"]), 1)
        result = catalog["masjids"][0]
        self.assertEqual(result["id"], "node/1")
        self.assertEqual(result["providers"]["masjidal"], "1234")
        self.assertIn("Example Masjid", result["aliases"])
        self.assertEqual(audit["mergedRecords"], 2)
        self.assertFalse(audit["completeCensus"])

    def test_shared_website_does_not_merge_distant_branches(self):
        a = row("node/1", "Downtown Masjid")
        b = row("usa/two", "Uptown Masjid", lat=40.8)
        catalog, _ = build_catalog(
            {"masjids": [a]},
            {"masjids": [b]},
            {"masjids": []},
            timezone_for=lambda *_: "America/New_York",
        )
        self.assertEqual(len(catalog["masjids"]), 2)

    def test_wrong_country_coordinates_are_quarantined_instead_of_guessed(self):
        invalid = row("masjidal/bad", "Bad coordinates", lat=-70, lng=40)
        catalog, audit = build_catalog(
            {"masjids": []},
            {"masjids": []},
            {"masjids": [invalid]},
            timezone_for=lambda *_: "Antarctica/Syowa",
        )
        self.assertEqual(catalog["masjids"], [])
        self.assertIn("outside US", audit["rejectedOrReview"][0]["reason"])

    def test_snapshot_is_attached_only_to_its_mapped_account(self):
        a = row("masjidal/a", "Example Masjid")
        a["providers"] = {"masjidal": "a"}
        snapshot = {"provider": "masjidal", "providerId": "other"}
        catalog, _ = build_catalog(
            {"masjids": []},
            {"masjids": []},
            {"masjids": [a]},
            timezone_for=lambda *_: "America/New_York",
            schedules={"other": snapshot},
        )
        self.assertNotIn("publishedSchedule", catalog["masjids"][0])


if __name__ == "__main__":
    unittest.main()
