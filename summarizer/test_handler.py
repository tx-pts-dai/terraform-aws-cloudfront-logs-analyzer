import sys
import time
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
assert r["ua_class"] == "bot" and r["robots_token"] == "Googlebot" and r["agent"].startswith("googlebot"), r

r = c("Mozilla/5.0%20(Linux;%20Android%206.0.1;%20Nexus%205X)%20AppleWebKit/537.36%20(compatible;%20Googlebot/2.1)")
assert r["robots_token"] == "Googlebot", r

r = c("AdsBot-Google%20(+http://www.google.com/adsbot.html)")
assert r["ua_class"] == "bot" and r["robots_token"] == "AdsBot-Google", r

r = c("Mozilla/5.0%20(iPhone;%20CPU%20iPhone%20OS%2018_7%20like%20Mac%20OS%20X)%20AppleWebKit/605.1.15%20(KHTML,%20like%20Gecko)%20Version/18.0%20Mobile/15E148%20Safari/604.1")
assert r["ua_class"] == "human" and r["os"] == "iOS" and r["device"] == "iPhone" and r["agent"] == "", r

r = c("Mozilla/5.0%20(X11;%20Linux%20x86_64)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20HeadlessChrome/120.0.0.0%20Safari/537.36")
assert r["agent"].startswith("headlesschrome"), r

r = c("python-requests/2.31.0")
assert r["agent"] == "python-requests" and r["robots_token"] == "", r

for empty in ("", "-"):
    r = c(empty)
    assert r["ua_class"] == "suspected_bot" and r["agent"] == "" and r["robots_token"] == "", r

t0 = time.perf_counter()
r = c("a" * 8192)
assert time.perf_counter() - t0 < 0.05 and r["ua_class"] == "human", r
t0 = time.perf_counter()
assert handler.find_agent("a" * 1_000_000 + " googlebot")[0] == "googlebot"
assert time.perf_counter() - t0 < 0.5
r = c("okhttp/4.9 MyCrawler/1.0")
assert r["ua_class"] == "bot" and r["agent"] == "mycrawler", r
r = c("Mozilla/5.0%20(Linux;%20Android%209;%20CUBOT_X19)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/99.0.4844.88%20Mobile%20Safari/537.36")
assert r["ua_class"] == "human" and r["agent"] == "", r
r = c("Mozilla/5.0%20(Windows%20NT%2010.0)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/100.0.4896.127%20Safari/537.36%20javascript")
assert r["ua_class"] == "human" and r["agent"] == "", r
assert c("Java/1.8.0_292")["ua_class"] == "bot"
r = c("okhttp/4.9.3")
assert r["ua_class"] == "suspected_bot" and r["agent"] == "okhttp", r
assert c("Googlebot/2.1")["agent"] == "googlebot"
assert handler.filter_url_tokens(["GPTBot", "Mozilla", "Gecko", "abc", "Sogou web spider", "Yandex"]) == ["GPTBot", "Sogou web spider"]

now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
assert handler.parse_event({}, now) == ("2025-12-31", 23, 23)
assert handler.parse_event({"dt": "2026-03-05", "hour": 7}, now) == ("2026-03-05", 7, 7)
assert handler.parse_event({"dt": "2026-03-05"}, now) == ("2026-03-05", 0, 23)
try:
    handler.parse_event({"reparse": True}, now)
    raise AssertionError("reparse accepted")
except ValueError:
    pass
for bad in ({"dt": "2026-3-5x"}, {"dt": "2026-03-05", "hour": 24}, {"dt": "2026-03-05", "hour": "1"}, {"dt": "2026-03-05", "hour": True}):
    try:
        handler.parse_event(bad, now)
    except ValueError:
        continue
    raise AssertionError(bad)

assert handler.range_params("2026-03-05", 0, 23) == ["'2026/03/05'", "0", "23"]
txt = "User-agent: *\r\nDisallow: /x\r\nUser-Agent: Sogou web spider\r\nuser-agent:GPTBot \r\nUSER-AGENT: GPTBot\r\n# user-agent: no\r\n"
assert handler.parse_robots_txt(txt) == ["Sogou web spider", "GPTBot"], handler.parse_robots_txt(txt)
assert handler.parse_robots_txt("") == []
assert handler.parse_robots_txt("User-agent: Foo # c\n  user-agent: Bar\nUser-agent:\n") == ["Foo", "Bar"]
assert handler.parse_event({"scheduled_time": "2026-11-10T00:20:00Z"}, now) == ("2026-11-09", 23, 23)
assert handler.parse_event({"scheduled_time": "2026-11-10T13:20:00Z"}, now) == ("2026-11-10", 12, 12)
assert handler.merge_tokens(["GPTBot", "Googlebot"], ["gptbot", "Sogou web spider"]) == ["GPTBot", "Googlebot", "Sogou web spider"]
assert handler.classify("Sogou web spider/4.0", ["Sogou web spider"])["robots_token"] == "Sogou web spider"
assert handler.unload_params("2026-03-05", 7, 7) == ["'2026-03-05'", "'2026/03/05'", "7", "7"]

CHROME = "Mozilla/5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/{}%20Safari/537.36"
assert c(CHROME.format("152.0.0.0"))["ua_class"] == "human"
assert c(CHROME.format("154.0.8037.99"))["ua_class"] == "suspected_bot"
assert c(CHROME.format("100.0.4896.127"))["ua_class"] == "human"
r = c("Mozilla/5.0%20(Linux;%20Android%2014;%20Pixel%207;%20wv)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Version/4.0%20Chrome/157.0.8103.38%20Mobile%20Safari/537.36")
assert r["ua_class"] == "human", r
assert c("Mozilla/5.0%20(Windows%20NT%2010.0)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20App/1.0%20Chrome/157.0.8103.38%20Electron/30.0.0%20Safari/537.36")["ua_class"] == "human"
assert c(CHROME.format("154.0.8037.99") + "%20Diffbot/0.1")["ua_class"] == "bot"
assert c("axios/1.13.2")["ua_class"] == "bot"
assert handler.parser_version(TOKENS).endswith("+" + handler.RULES_VERSION)
assert c("Mozilla/5.0%20(Linux;%20Android%2014)%20AppleWebKit/537.36%20Version/4.0%20Chrome/157.0.8103.38%20Mobile%20Safari/537.36")["ua_class"] == "human"

calls: list = []
handler.stream_results = lambda qid: iter([["1", "a"], ["1", "b"], ["3", "c"]])
handler.run_query = lambda *a, **k: "q"
handler.list_keys = lambda p: [p + "old.gz"] if p.endswith("hour=02/") else []
handler.delete_keys = lambda ks: calls.append(("del", ks))
handler.s3 = mock.MagicMock()
handler.s3.put_object.side_effect = lambda **k: calls.append(("put", k["Key"]))
assert handler.write_ua_map("2026-03-05", 0, 3, TOKENS) == 3
puts = [x[1] for x in calls if x[0] == "put"]
assert puts == ["ua_map/dt=2026-03-05/hour=01/map.jsonl.gz", "ua_map/dt=2026-03-05/hour=03/map.jsonl.gz"], puts
assert ("del", ["ua_map/dt=2026-03-05/hour=02/old.gz"]) in calls, calls
calls.clear()
handler.stream_results = lambda qid: iter([])
try:
    handler.write_ua_map("2026-03-05", 0, 3, TOKENS)
    raise AssertionError("empty range accepted")
except RuntimeError:
    assert not calls, calls

store = {"summaries/cf_ua_hourly/dt=2026-03-05/hour=01/old.parquet", "summaries/cf_bot_errors_hourly/dt=2026-03-05/hour=01/old.parquet"}
handler.list_keys = lambda p: sorted(k for k in store if k.startswith(p))
handler.delete_keys = lambda ks: store.difference_update(ks)


def unload_ok(sql: str, params: list, deadline: float | None = None) -> str:
    table = "cf_ua_hourly" if "summaries/cf_ua_hourly/" in sql else "cf_bot_errors_hourly"
    store.add(f"summaries/{table}/dt=2026-03-05/hour=01/new.parquet")
    return "q"


handler.run_query = unload_ok
handler.rebuild_summaries("2026-03-05", 1, 1)
assert store == {
    "summaries/cf_ua_hourly/dt=2026-03-05/hour=01/new.parquet",
    "summaries/cf_bot_errors_hourly/dt=2026-03-05/hour=01/new.parquet",
}, store

store.clear()
store.update({"summaries/cf_ua_hourly/dt=2026-03-05/hour=01/old.parquet", "summaries/cf_bot_errors_hourly/dt=2026-03-05/hour=01/old.parquet"})
before = set(store)


def unload_fail(sql: str, params: list, deadline: float | None = None) -> str:
    store.add("summaries/cf_ua_hourly/dt=2026-03-05/hour=01/partial.parquet")
    raise RuntimeError("unload failed")


handler.run_query = unload_fail
try:
    handler.rebuild_summaries("2026-03-05", 1, 1)
    raise AssertionError("failed UNLOAD swallowed")
except RuntimeError:
    assert store == before, store
print("ok")


class FakeResp:
    def __init__(self, data: bytes = b"", url: str = "https://x.test/robots.txt") -> None:
        self.data, self.url, self.calls = data, url, 0

    def read1(self, n: int) -> bytes:
        self.calls += 1
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk

    def __enter__(self) -> "FakeResp":
        return self

    def __exit__(self, *a: object) -> None:
        return None


big = FakeResp(b"x" * (handler.ROBOTS_MAX_BYTES + 5000))
assert len(handler.read_capped(big)) == handler.ROBOTS_MAX_BYTES and big.calls == 16

with mock.patch.object(handler, "ROBOTS_URL", "http://x.test/robots.txt"), mock.patch.object(handler.urllib.request, "urlopen") as uo:
    assert handler.url_tokens() == []
    uo.assert_not_called()

with mock.patch.object(handler, "ROBOTS_URL", "https://x.test/robots.txt"):
    with mock.patch.object(handler.urllib.request, "urlopen", return_value=FakeResp(b"User-agent: GPTBot\n")):
        assert handler.url_tokens() == ["GPTBot"]
    with mock.patch.object(handler.urllib.request, "urlopen", return_value=FakeResp(b"User-agent: GPTBot\n", "http://x.test/robots.txt")):
        assert handler.url_tokens() == []
