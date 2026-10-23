import re
from datetime import datetime

from macroflow import timeutil


def test_utc_now_iso_matches_legacy_utcnow_format(monkeypatch):
    s = timeutil.utc_now_iso()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z", s)
    assert datetime.fromisoformat(s).utcoffset().total_seconds() == 0


def test_utc_now_iso_omits_zero_microseconds_like_isoformat(monkeypatch):
    real = timeutil.datetime

    class Fixed(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 1, 1, 0, 0, 0, tzinfo=tz)

    monkeypatch.setattr(timeutil, "datetime", Fixed)
    assert timeutil.utc_now_iso() == "2026-01-01T00:00:00Z"
