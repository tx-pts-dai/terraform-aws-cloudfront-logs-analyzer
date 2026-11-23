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
assert time.perf_counter() - t0 < 0.05 and r["ua_class"] == "suspected_bot", r
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

now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
assert handler.parse_event_date({}, now) == ("2025-12-31", 23, 23)
assert handler.parse_event_date({"dt": "2026-03-05", "hour": 7}, now) == ("2026-03-05", 7, 7)
assert handler.parse_event_date({"dt": "2026-03-05"}, now) == ("2026-03-05", 0, 23)
for bad in ({"dt": "2026-3-5x"}, {"dt": "2026-03-05", "hour": 24}, {"dt": "2026-03-05", "hour": "1"}, {"dt": "2026-03-05", "hour": True}):
    try:
        handler.parse_event_date(bad, now)
    except ValueError:
        continue
    raise AssertionError(bad)

assert handler.range_params("2026-03-05", 0, 23) == ["'2026/03/05'", "0", "23"]
assert handler.parse_event_date({"scheduled_time": "2026-11-10T00:20:00Z"}, now) == ("2026-11-09", 23, 23)
assert handler.parse_event_date({"scheduled_time": "2026-11-10T13:20:00Z"}, now) == ("2026-11-10", 12, 12)
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
for ua in ("Datadog%20Agent/7.84.1", "node", "Alinea/2%20(personal%20article%20reader)"):
    assert c(ua)["ua_class"] == "suspected_bot", ua
for ua in ("akka-http/10.1.10", "got%20(https://github.com/sindresorhus/got)", "Feeder/1.0%20(feeder.co/fetcher)", "PostmanRuntime/7.20.1"):
    assert c(ua)["ua_class"] == "bot", ua
for ua in (
    "NetworkingExtension/8625.2.8.10.7%20Network/6681.42.3%20iOS/27.2.1",
    "com.apple.WebKit.Networking/22625.2.8.11.9%20Network/6681.41.1%20macOS/27.2.1",
    CHROME.format("125.0.0.0"),
    "Dalvik/2.1.0%20(Linux;%20U;%20Android%2017;%20SM-S911B%20Build/CP2A.260605.016)",
    "Reeder/5060003%20CFNetwork/3896.200.61%20Darwin/27.2.0",
):
    assert c(ua)["ua_class"] == "human", ua
r = handler.classify("Mediapartners-Google", TOKENS + ["Mediapartners-Google"])
assert r["ua_class"] == "bot" and r["robots_token"] == "Mediapartners-Google", r
assert handler.parser_version(TOKENS).endswith("+" + handler.RULES_VERSION)
assert c("Mozilla/5.0%20(Linux;%20Android%2014)%20AppleWebKit/537.36%20Version/4.0%20Chrome/157.0.8103.38%20Mobile%20Safari/537.36")["ua_class"] == "human"

calls: list = []
handler.stream_results = lambda qid: iter([["1", "a"], ["1", "b"], ["3", "c"]])
handler.run_query = lambda *a, **k: "q"
handler.list_keys = lambda p: [p + "old.gz"] if p.endswith("hour=02/") else []
handler.delete_keys = lambda ks: calls.append(("del", ks))
handler.s3 = mock.MagicMock()
handler.s3.put_object.side_effect = lambda **k: calls.append(("put", k["Key"]))
assert handler.write_ua_map("2026-03-05", 0, 3, TOKENS) == (3, {1, 3})
puts = [x[1] for x in calls if x[0] == "put"]
assert puts == ["ua_map/dt=2026-03-05/hour=01/map.jsonl.gz", "ua_map/dt=2026-03-05/hour=03/map.jsonl.gz"], puts
assert not any("hour=02/" in k for x in calls if x[0] == "del" for k in x[1]), calls
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
handler.rebuild_summaries("2026-03-05", 1, 1, {1})
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
    handler.rebuild_summaries("2026-03-05", 1, 1, {1})
    raise AssertionError("failed UNLOAD swallowed")
except RuntimeError:
    assert store == before, store

store.clear()
store.update(f"summaries/{t}/dt=2026-03-05/hour={h:02d}/old.parquet" for t in handler.SUMMARIES for h in range(24))
before = set(store)


def unload_new(sql: str, params: list, deadline: float | None = None) -> str:
    table = "cf_ua_hourly" if "summaries/cf_ua_hourly/" in sql else "cf_bot_errors_hourly"
    store.update(f"summaries/{table}/dt=2026-03-05/hour={h:02d}/new.parquet" for h in range(24))
    return "q"


handler.run_query = unload_new
handler.rebuild_summaries("2026-03-05", 0, 23, {23})
assert before - store == {k for k in before if "hour=23/" in k}, before - store

store.clear()
store.update(before)


def unload_partial(sql: str, params: list, deadline: float | None = None) -> str:
    store.update(f"summaries/cf_ua_hourly/dt=2026-03-05/hour={h:02d}/partial.parquet" for h in (5, 23))
    raise RuntimeError("unload failed")


handler.run_query = unload_partial
try:
    handler.rebuild_summaries("2026-03-05", 0, 23, {23})
    raise AssertionError("failed UNLOAD swallowed")
except RuntimeError:
    assert store == before, store
print("ok")
