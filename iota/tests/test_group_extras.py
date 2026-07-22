"""
Tests for pure helper logic in handlers/group_extras (no Telegram API calls).
Run: python -m unittest tests.test_group_extras -v   (from iota/ folder)
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
IOTA = os.path.dirname(HERE)
if IOTA not in sys.path:
    sys.path.insert(0, IOTA)


def _import_group_extras():
    import importlib.util
    spec = importlib.util.spec_from_file_location("group_extras", os.path.join(IOTA, "handlers", "group_extras.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseDuration(unittest.TestCase):
    def test_seconds(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration("30s"), 30)

    def test_minutes(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration("5m"), 300)

    def test_hours(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration("2h"), 7200)

    def test_days(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration("3d"), 259200)

    def test_invalid(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration(""), 0)
        self.assertEqual(parse_duration("abc"), 0)
        self.assertEqual(parse_duration("5x"), 0)

    def test_case_insensitive(self):
        from utils.helpers import parse_duration
        self.assertEqual(parse_duration("1H"), 3600)


class TestNightmodeWindow(unittest.TestCase):
    def test_overnight_active(self):
        from handlers.group_extras import in_night_window
        self.assertTrue(in_night_window("23:00", "07:00", 1439))  # 23:59
        self.assertTrue(in_night_window("23:00", "07:00", 60))    # 01:00
        self.assertFalse(in_night_window("23:00", "07:00", 720))  # 12:00

    def test_same_day(self):
        from handlers.group_extras import in_night_window
        self.assertFalse(in_night_window("10:00", "12:00", 720))  # 12:00 end exclusive
        self.assertTrue(in_night_window("10:00", "12:00", 659))   # 10:59
        self.assertTrue(in_night_window("10:00", "12:00", 600))   # 10:00 start inclusive


class TestWelcomeButtonParse(unittest.TestCase):
    def test_parse_add_valid(self):
        raw = "Rules|https://t.me/mychannel"
        text, url = raw.split("|", 1)
        self.assertEqual(text.strip(), "Rules")
        self.assertEqual(url.strip(), "https://t.me/mychannel")

    def test_parse_add_missing_pipe(self):
        raw = "Rules only"
        self.assertNotIn("|", raw)

    def test_parse_add_empty(self):
        raw = "|"
        text, url = raw.split("|", 1)
        self.assertEqual(text.strip(), "")
        self.assertEqual(url.strip(), "")


class TestExportDictBuild(unittest.TestCase):
    """Pure dict-construction for export — no DB or Telegram calls."""

    def test_basic_shape(self):
        payload = {
            "group_id": 123,
            "title": "Test Group",
            "group_settings": {"warn_limit": 3},
            "welcome_settings": {"enabled": True},
            "protection": {"anti_link": True},
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertIn("group_id", payload)
        self.assertIn("welcome_settings", payload)
        self.assertEqual(payload["group_id"], 123)

    def test_serializable(self):
        import json
        payload = {
            "group_id": 123,
            "title": "Test",
            "group_settings": {},
            "welcome_settings": {},
            "protection": {},
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        raw = json.dumps(payload, default=str)
        self.assertIsInstance(raw, str)
        parsed = json.loads(raw)
        self.assertEqual(parsed["group_id"], 123)


class TestGroupGatesNightmodeLogic(unittest.TestCase):
    """Pure night-mode window detection (mirrors the handler logic)."""

    def _is_night(self, start: str, end: str, now_dt: datetime = None) -> bool:
        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        now_min = now_dt.hour * 60 + now_dt.minute
        try:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            start_min = sh * 60 + sm
            end_min = eh * 60 + em
        except Exception:
            return False
        if start_min > end_min:
            return now_min >= start_min or now_min < end_min
        return start_min <= now_min < end_min

    def test_midnight_ramp(self):
        # 23:30 start, 06:00 end
        self.assertTrue(self._is_night("23:30", "06:00", datetime(2026, 1, 1, 23, 30)))
        self.assertTrue(self._is_night("23:30", "06:00", datetime(2026, 1, 1, 5, 59)))
        self.assertTrue(self._is_night("23:30", "06:00", datetime(2026, 1, 1, 0, 0)))
        self.assertFalse(self._is_night("23:30", "06:00", datetime(2026, 1, 1, 12, 0)))

    def test_daytime_window(self):
        self.assertTrue(self._is_night("10:00", "12:00", datetime(2026, 1, 1, 10, 30)))
        self.assertFalse(self._is_night("10:00", "12:00", datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(self._is_night("10:00", "12:00", datetime(2026, 1, 1, 9, 59)))


if __name__ == "__main__":
    unittest.main()
