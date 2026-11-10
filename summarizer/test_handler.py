import sys
from datetime import datetime, timezone
from unittest import mock

try:
    import boto3  # noqa: F401
except ImportError:
    sys.modules["boto3"] = mock.MagicMock()

import handler

TOKENS = ["Googlebot", "Google", "AdsBot-Google"]


def c(ua: str) -> dict:
    return handler.classify(ua, TOKENS)


r = c("Mozilla/5.0%20(compatible;%20Googlebot/2.1;%20+http://www.google.com/bot.html)")
assert r["is_bot"] and r["robots_token"] == "Googlebot" and r["agent"].startswith("googlebot"), r

r = c("Mozilla/5.0%20(Linux;%20Android%206.0.1;%20Nexus%205X)%20AppleWebKit/537.36%20(compatible;%20Googlebot/2.1)")
assert r["robots_token"] == "Googlebot", r

r = c("AdsBot-Google%20(+http://www.google.com/adsbot.html)")
assert r["is_bot"] and r["robots_token"] == "AdsBot-Google", r

r = c("Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2018_7%20like%20Mac%20OS%20X)%20AppleWebKit/605.1.15%20(KHTML,%20like%20Gecko)%20Version/18.0%20Mobile/15E148%20Safari/604.1")
assert not r["is_bot"] and r["os"] == "iOS" and r["device"] == "iPhone" and r["agent"] == "", r
assert "Mozilla/5.0 (iPhone;" in r["ua"]

r = c("Mozilla/5.0%20(X11;%20Linux%20x86_64)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20HeadlessChrome/120.0.0.0%20Safari/537.36")
assert r["agent"].startswith("headlesschrome"), r

r = c("python-requests/2.31.0")
assert r["agent"] == "python-requests" and r["robots_token"] == "", r

for empty in ("", "-"):
    r = c(empty)
    assert not r["is_bot"] and r["agent"] == "" and r["robots_token"] == "", r

now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
assert handler.parse_event({}, now) == ("2025-12-31", 23, 23)
assert handler.parse_event({"dt": "2026-03-05", "hour": 7}, now) == ("2026-03-05", 7, 7)
assert handler.parse_event({"dt": "2026-03-05"}, now) == ("2026-03-05", 0, 23)
assert handler.parse_event({"reparse": True}, now) is None
for bad in ({"dt": "2026-3-5x"}, {"dt": "2026-03-05", "hour": 24}, {"dt": "2026-03-05", "hour": "1"}, {"dt": "2026-03-05", "hour": True}):
    try:
        handler.parse_event(bad, now)
    except ValueError:
        continue
    raise AssertionError(bad)

assert handler.range_params("2026-03-05", 0, 23) == ["'2026/03/05'", "0", "23"]
print("ok")
