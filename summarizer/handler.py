"""Hourly UA summarizer Lambda.

Scheduled hourly; processes the previous UTC hour. Finds user agents not yet in
ua_dim that reached MIN_DAILY_REQUESTS requests in the UTC day so far, classifies
them with ua-parser and the robots_agents tokens (requests from UAs below the
threshold stay in cf_ua_hourly unclassified), and appends
them as gzip JSONL to ua_dim/. Then deletes and re-UNLOADs the hour partitions of
cf_ua_hourly and cf_bot_errors_hourly.

Events:
  {}                            previous hour
  {"dt": "YYYY-MM-DD", "hour": H}  one hour
  {"dt": "YYYY-MM-DD"}          whole day
  {"reparse": true}             rewrite ua_dim with the current parser/tokens

Tokens come from the explicit robots_agents JSONL plus, when ROBOTS_URL is set,
the user-agents of that robots.txt (last good copy kept in from_url.json).
"""
import codecs
import csv
import gzip
import hashlib
import json
import os
import re
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

import boto3
from ua_parser import parse

DATABASE = os.environ.get("DATABASE", "")
WORKGROUP = os.environ.get("WORKGROUP", "")
OUTPUT_LOCATION = os.environ.get("OUTPUT_LOCATION", "")
RESULTS_BUCKET = os.environ.get("RESULTS_BUCKET", "")
ROBOTS_KEY = os.environ.get("ROBOTS_KEY", "")
ROBOTS_URL = os.environ.get("ROBOTS_URL", "")
MIN_DAILY_REQUESTS = int(os.environ.get("MIN_DAILY_REQUESTS", "100"))
ROBOTS_FROM_URL_KEY = "supporters/robots-agents/from_url.json"

UA_DIM_PREFIX = "ua_dim/"
REPARSE_CHUNK = 100_000
USER_AGENT_LINE_RE = re.compile(r"^\s*user-agent:([^#\r\n]*)", re.I | re.M)
SUMMARIES = ("cf_ua_hourly", "cf_bot_errors_hourly")

AGENT_RE = re.compile(
    r"([a-z0-9_.-]*(bot|crawler|spider|headless|lightpanda|thinkers|magentanews|python|curl|wget"
    r"|go-http|okhttp|java|axios|node-fetch|scrapy)[a-z0-9_.-]*)"
)

NEW_UAS_SQL = """
SELECT r.cs_user_agent, min(r.hour) AS first_hour
FROM cloudfront_logs_parquet r
LEFT JOIN ua_dim d ON r.cs_user_agent = d.cs_user_agent
WHERE r.dt = ? AND r.hour <= ?
  AND r.cs_user_agent IS NOT NULL AND d.cs_user_agent IS NULL
GROUP BY r.cs_user_agent
HAVING count(*) >= ?
"""

# partitioned_by columns must be last in the SELECT; hour is zero-padded to match the "hour=HH" projection template
UNLOAD_SQL = {
    "cf_ua_hourly": """
UNLOAD (
  SELECT distributionid, x_host_header AS host, cs_user_agent,
         concat(substr(sc_status, 1, 1), 'xx') AS status_class,
         x_edge_result_type AS edge_result_type,
         count(*) AS requests,
         sum(try_cast(sc_bytes AS bigint)) AS bytes,
         sum(try_cast(time_taken AS double)) AS time_taken_sum,
         replace(dt, '/', '-') AS dt,
         lpad(cast(hour AS varchar), 2, '0') AS hour
  FROM cloudfront_logs_parquet
  WHERE dt = ? AND hour BETWEEN ? AND ?
  GROUP BY 1, 2, 3, 4, 5, 9, 10
) TO '{location}'
WITH (format = 'PARQUET', compression = 'SNAPPY', partitioned_by = ARRAY['dt', 'hour'])
""",
    "cf_bot_errors_hourly": """
UNLOAD (
  SELECT r.distributionid, r.x_host_header AS host, r.cs_uri_stem AS path,
         r.cs_user_agent, r.sc_status,
         count(*) AS requests,
         replace(r.dt, '/', '-') AS dt,
         lpad(cast(r.hour AS varchar), 2, '0') AS hour
  FROM cloudfront_logs_parquet r
  JOIN (SELECT cs_user_agent FROM ua_dim WHERE is_bot OR agent <> '' GROUP BY cs_user_agent) u
    ON r.cs_user_agent = u.cs_user_agent
  WHERE r.dt = ? AND r.hour BETWEEN ? AND ?
    AND substr(r.sc_status, 1, 1) IN ('4', '5')
  GROUP BY 1, 2, 3, 4, 5, 7, 8
) TO '{location}'
WITH (format = 'PARQUET', compression = 'SNAPPY', partitioned_by = ARRAY['dt', 'hour'])
""",
}

s3 = boto3.client("s3")
athena = boto3.client("athena")


def parse_event(event: dict[str, Any], now: datetime) -> tuple[str, int, int] | None:
    """Return (dt 'YYYY-MM-DD', first_hour, last_hour), or None for a reparse."""
    if event.get("reparse") is True:
        return None
    if "dt" not in event:
        if "scheduled_time" in event:
            now = datetime.fromisoformat(event["scheduled_time"])
        prev = now.astimezone(timezone.utc) - timedelta(hours=1)
        return prev.strftime("%Y-%m-%d"), prev.hour, prev.hour
    dt = datetime.strptime(event["dt"], "%Y-%m-%d").strftime("%Y-%m-%d")
    if "hour" not in event:
        return dt, 0, 23
    hour = event["hour"]
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError(f"hour must be an integer 0-23, got {hour!r}")
    return dt, hour, hour


def match_token(ua: str, tokens: list[str]) -> str:
    """Return the longest robots token contained in the UA, or ''."""
    low = ua.lower()
    hits = [t for t in tokens if t.lower() in low]
    return max(hits, key=len) if hits else ""


def classify(raw: str, tokens: list[str]) -> dict[str, Any]:
    """Classify a raw user agent into a ua_dim row."""
    ua = unquote(raw)
    r = parse(ua).with_defaults()
    m = AGENT_RE.search(ua.lower())
    return {
        "cs_user_agent": raw,
        "ua": ua,
        "family": r.user_agent.family,
        "os": r.os.family,
        "device": r.device.family,
        "is_bot": r.device.family == "Spider",
        "agent": m.group(1) if m else "",
        "robots_token": match_token(ua, tokens),
    }


def parser_version(tokens: list[str]) -> str:
    """Version string from the ua-parser release and a hash of the tokens."""
    digest = hashlib.sha256("\n".join(sorted(tokens)).encode()).hexdigest()[:8]
    return f"ua-parser-{version('ua-parser')}+builtins-{version('ua-parser-builtins')}+{digest}"


def parse_robots_txt(text: str) -> list[str]:
    """Return distinct user-agent tokens of a robots.txt, in order, without '*'."""
    found = (m.strip() for m in USER_AGENT_LINE_RE.findall(text))
    return list(dict.fromkeys(t for t in found if t and t != "*"))


def merge_tokens(explicit: list[str], from_url: list[str]) -> list[str]:
    """Explicit tokens first; URL tokens are added unless equal (case-insensitive) to an explicit one."""
    seen = {t.lower() for t in explicit}
    return explicit + [t for t in from_url if t.lower() not in seen]


def read_tokens(key: str) -> list[str] | None:
    """Read tokens from a robots_agents JSONL in S3; None if the key does not exist."""
    try:
        body = s3.get_object(Bucket=RESULTS_BUCKET, Key=key)["Body"].read().decode()
    except s3.exceptions.NoSuchKey:
        return None
    return [json.loads(line)["token"] for line in body.splitlines() if line.strip()]


def url_tokens(explicit: list[str]) -> list[str]:
    """Fetch tokens from ROBOTS_URL and store them; on failure use the last stored copy."""
    if not ROBOTS_URL:
        return []
    try:
        if urlparse(ROBOTS_URL).scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme in {ROBOTS_URL}")
        req = urllib.request.Request(ROBOTS_URL, headers={"User-Agent": "cf-logs-summarizer"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            tokens = parse_robots_txt(resp.read().decode("utf-8", errors="replace"))
        if not tokens:
            raise ValueError("no user-agent entries found")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: robots.txt fetch failed ({e}); using last stored copy")
        return read_tokens(ROBOTS_FROM_URL_KEY) or []
    seen = {t.lower() for t in explicit}
    rows = [
        {"token": t, "operator": "", "purpose": "other", "notes": "from robots_agents_url"}
        for t in tokens
        if t.lower() not in seen
    ]
    body = "".join(json.dumps(r) + "\n" for r in rows)
    s3.put_object(Bucket=RESULTS_BUCKET, Key=ROBOTS_FROM_URL_KEY, Body=body.encode())
    return [r["token"] for r in rows]


def load_tokens() -> list[str]:
    """Explicit robots tokens from S3 merged with those of ROBOTS_URL."""
    explicit = read_tokens(ROBOTS_KEY) or []
    return merge_tokens(explicit, url_tokens(explicit))


def run_query(sql: str, params: list[str] | None = None, deadline: float | None = None) -> str:
    """Run an Athena query and wait for it; stop it and raise if the monotonic deadline passes."""
    kwargs: dict[str, Any] = {
        "QueryString": sql,
        "QueryExecutionContext": {"Database": DATABASE},
        "WorkGroup": WORKGROUP,
        "ResultConfiguration": {"OutputLocation": OUTPUT_LOCATION},
    }
    if params:
        kwargs["ExecutionParameters"] = params
    qid = athena.start_query_execution(**kwargs)["QueryExecutionId"]
    delay = 1.0
    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena query {qid} {state}: {status.get('StateChangeReason', '')}")
        if deadline is not None and time.monotonic() > deadline:
            athena.stop_query_execution(QueryExecutionId=qid)
            raise RuntimeError(f"Athena query {qid} stopped: Lambda out of time")
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def fetch_rows(qid: str) -> list[list[str]]:
    """Return result rows of a query, without the header."""
    rows: list[list[str]] = []
    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
    for page in pages:
        rows += [[c.get("VarCharValue", "") for c in r["Data"]] for r in page["ResultSet"]["Rows"]]
    return rows[1:]


def range_params(dt: str, lo: int, hi: int) -> list[str]:
    """Build Athena parameters for a dt and hour range."""
    return [f"'{dt.replace('-', '/')}'", str(lo), str(hi)]


def new_uas_params(dt: str, hi: int) -> list[str]:
    """Build Athena parameters for NEW_UAS_SQL: the day so far up to hour hi."""
    return [f"'{dt.replace('-', '/')}'", str(hi), str(MIN_DAILY_REQUESTS)]


def list_keys(prefix: str) -> list[str]:
    """List all S3 keys under a prefix."""
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=RESULTS_BUCKET, Prefix=prefix)
    return [o["Key"] for p in pages for o in p.get("Contents", [])]


def delete_keys(keys: list[str]) -> None:
    """Delete S3 keys in batches of 1000."""
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        s3.delete_objects(Bucket=RESULTS_BUCKET, Delete={"Objects": batch, "Quiet": True})


def write_ua_dim(rows: list[dict[str, Any]], name: str) -> str:
    """Write rows as one gzip JSONL object under ua_dim/ and return its key."""
    key = f"{UA_DIM_PREFIX}{name}.jsonl.gz"
    body = "".join(json.dumps(r) + "\n" for r in rows)
    s3.put_object(Bucket=RESULTS_BUCKET, Key=key, Body=gzip.compress(body.encode()))
    return key


def add_new_uas(dt: str, lo: int, hi: int, tokens: list[str], deadline: float | None = None) -> int:
    """Classify UAs not yet in ua_dim that reached MIN_DAILY_REQUESTS in the day so far; return the count."""
    rows = fetch_rows(run_query(NEW_UAS_SQL, new_uas_params(dt, hi), deadline))
    pv = parser_version(tokens)
    out = [
        classify(raw, tokens) | {"parser_version": pv, "first_seen": f"{dt} {int(h):02d}"}
        for raw, h in rows
    ]
    if out:
        write_ua_dim(out, f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}")
    return len(out)


REPARSE_SQL = """
SELECT cs_user_agent, min(first_seen) AS first_seen
FROM ua_dim
WHERE cs_user_agent IS NOT NULL
GROUP BY 1
"""


def stream_results(qid: str) -> Iterator[list[str]]:
    """Yield the rows of a query result CSV from S3, without the header."""
    out = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
    u = urlparse(out)
    body = s3.get_object(Bucket=u.netloc, Key=u.path.lstrip("/"))["Body"]
    reader = csv.reader(codecs.getreader("utf-8")(body))
    next(reader, None)
    yield from reader


def reparse(tokens: list[str], deadline: float | None = None) -> int:
    """Re-classify all ua_dim rows in constant memory; return the row count."""
    old_keys = list_keys(UA_DIM_PREFIX)
    pv = parser_version(tokens)
    run_id = uuid.uuid4().hex[:8]
    qid = run_query(REPARSE_SQL, deadline=deadline)
    new_keys: list[str] = []
    chunk: list[dict[str, Any]] = []
    total = 0

    def flush() -> None:
        nonlocal chunk, total
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError("reparse stopped: Lambda out of time")
        new_keys.append(write_ua_dim(chunk, f"reparse-{run_id}-{len(new_keys):04d}"))
        total += len(chunk)
        chunk = []

    try:
        for ua, first_seen in stream_results(qid):
            chunk.append(classify(ua, tokens) | {"parser_version": pv, "first_seen": first_seen})
            if len(chunk) == REPARSE_CHUNK:
                flush()
        if chunk:
            flush()
    except BaseException:
        delete_keys(new_keys)
        raise
    if new_keys:
        delete_keys(old_keys)
    return total


def rebuild_summaries(dt: str, lo: int, hi: int, deadline: float | None = None) -> None:
    """Delete and re-UNLOAD the summary partitions for the range."""
    for table in SUMMARIES:
        delete_keys(
            [k for h in range(lo, hi + 1) for k in list_keys(f"summaries/{table}/dt={dt}/hour={h:02d}/")]
        )
        location = f"s3://{RESULTS_BUCKET}/summaries/{table}/"
        run_query(UNLOAD_SQL[table].format(location=location), range_params(dt, lo, hi), deadline)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    tokens = load_tokens()
    target = parse_event(event, datetime.now(timezone.utc))
    deadline = time.monotonic() + context.get_remaining_time_in_millis() / 1000 - 20
    if target is None:
        return {"reparsed": reparse(tokens, deadline)}
    dt, lo, hi = target
    added = add_new_uas(dt, lo, hi, tokens, deadline)
    rebuild_summaries(dt, lo, hi, deadline)
    return {"dt": dt, "hours": [lo, hi], "new_uas": added}
