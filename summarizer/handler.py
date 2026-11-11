"""Hourly UA summarizer Lambda.

Scheduled hourly; processes the previous UTC hour. Reads the distinct user agents
of the range from the raw logs, classifies each with ua-parser and the robots_agents
tokens (ua_class: bot, suspected_bot, human) and writes one gzip JSONL per hour to
ua_map/dt=YYYY-MM-DD/hour=HH/map.jsonl.gz (overwritten on re-run). Then re-UNLOADs the
hour partitions of cf_ua_hourly and cf_bot_errors_hourly, which join the raw logs to
ua_map, and deletes the files they replace once the UNLOAD succeeded.

Events:
  {}                            previous hour
  {"dt": "YYYY-MM-DD", "hour": H}  one hour
  {"dt": "YYYY-MM-DD"}          whole day

Tokens come from the robots_agents JSONL in S3, which Terraform builds from
summaries.robots_agents plus the user-agents of summaries.robots_agents_url.
"""
import codecs
import csv
import gzip
import hashlib
import json
import os
import re
import time
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

RULES_VERSION = "rules-2"
CHROME_RE = re.compile(r"Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)")
SUMMARIES = ("cf_ua_hourly", "cf_bot_errors_hourly")
UA_MAX_LEN = 1024
AGENT_CTX = 64

# okhttp is also used by human-driven apps, so it only makes a suspected_bot
KEY_RE = re.compile(
    r"(?<!cu)bot|crawler|spider|headless|lightpanda|thinkers|magentanews|python|curl|wget"
    r"|go-http|java(?!script)|axios|node-fetch|scrapy|(?P<weak>okhttp)"
)
AGENT_HEAD_RE = re.compile(r"[a-z0-9_.-]*$")
AGENT_TAIL_RE = re.compile(r"[a-z0-9_.-]{0,%d}" % AGENT_CTX)

MAP_SQL = """
SELECT hour, coalesce(cs_user_agent, '-') AS cs_user_agent
FROM cloudfront_logs_parquet
WHERE dt = ? AND hour BETWEEN ? AND ?
GROUP BY 1, 2
ORDER BY 1
"""

# Parameters: map dt (YYYY-MM-DD), raw dt (YYYY/MM/DD), first hour, last hour.
# partitioned_by columns must be last in the SELECT; hour is zero-padded to match the "hour=HH" projection template.
# LEFT JOIN keeps rows of late-arriving logs that the map query did not see (ua_class 'unmapped').
# bot_ua is only set for declared bots, so raw strings are kept for them alone.
JOIN_SQL = """
  FROM cloudfront_logs_parquet r
  LEFT JOIN ua_map m
    ON m.dt = ? AND m.hour = lpad(cast(r.hour AS varchar), 2, '0')
   AND m.cs_user_agent = coalesce(r.cs_user_agent, '-')
  WHERE r.dt = ? AND r.hour BETWEEN ? AND ?
"""

UNLOAD_SQL = {
    "cf_ua_hourly": """
UNLOAD (
  SELECT r.distributionid, r.x_host_header AS host, r.c_country AS country,
         coalesce(m.ua_class, 'unmapped') AS ua_class, coalesce(m.family, '') AS family,
         coalesce(m.os, '') AS os, coalesce(m.device, '') AS device,
         coalesce(m.agent, '') AS agent, coalesce(m.robots_token, '') AS robots_token,
         concat(substr(r.sc_status, 1, 1), 'xx') AS status_class,
         r.x_edge_result_type AS edge_result_type,
         coalesce(m.parser_version, '') AS parser_version,
         CASE WHEN m.ua_class = 'bot' THEN m.cs_user_agent END AS bot_ua,
         count(*) AS requests,
         sum(try_cast(r.sc_bytes AS bigint)) AS bytes,
         sum(try_cast(r.time_taken AS double)) AS time_taken_sum,
         cast(approx_set(coalesce(r.cs_user_agent, '-')) AS varbinary) AS uas_hll,
         arbitrary(coalesce(r.cs_user_agent, '-')) AS sample_ua,
         replace(r.dt, '/', '-') AS dt,
         lpad(cast(r.hour AS varchar), 2, '0') AS hour
""" + JOIN_SQL + """  GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 19, 20
) TO '{location}'
WITH (format = 'PARQUET', compression = 'SNAPPY', partitioned_by = ARRAY['dt', 'hour'])
""",
    "cf_bot_errors_hourly": """
UNLOAD (
  SELECT r.distributionid, r.x_host_header AS host, r.cs_uri_stem AS path,
         r.c_country AS country, m.ua_class, m.family, m.os, m.agent, m.robots_token,
         CASE WHEN m.ua_class = 'bot' THEN m.cs_user_agent END AS bot_ua,
         r.sc_status,
         count(*) AS requests,
         replace(r.dt, '/', '-') AS dt,
         lpad(cast(r.hour AS varchar), 2, '0') AS hour
""" + JOIN_SQL + """    AND substr(r.sc_status, 1, 1) IN ('4', '5')
    AND m.ua_class IN ('bot', 'suspected_bot')
  GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14
) TO '{location}'
WITH (format = 'PARQUET', compression = 'SNAPPY', partitioned_by = ARRAY['dt', 'hour'])
""",
}

s3 = boto3.client("s3")
athena = boto3.client("athena")


def parse_event(event: dict[str, Any], now: datetime) -> tuple[str, int, int]:
    """Return (dt 'YYYY-MM-DD', first_hour, last_hour)."""
    if "reparse" in event:
        raise ValueError("reparse is no longer supported; re-run with {dt} instead")
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


def is_suspected_bot(ua: str) -> bool:
    """True for a full-version Chrome token (Chrome >= 101 sends X.0.0.0) outside webviews and Electron."""
    m = CHROME_RE.search(ua)
    if not m or int(m.group(1)) < 101 or m.groups()[1:] == ("0", "0", "0"):
        return False
    return not ("; wv)" in ua or "Version/4.0 Chrome" in ua or "Electron/" in ua)


def find_agent(low: str) -> tuple[str, bool]:
    """Return the [a-z0-9_.-] word around the first strong keyword hit, else the first weak one ('' if none), and whether it is weak."""
    hits = list(KEY_RE.finditer(low))
    m = next((h for h in hits if h.group("weak") is None), hits[0] if hits else None)
    if not m:
        return "", False
    head = AGENT_HEAD_RE.search(low[max(0, m.start() - AGENT_CTX) : m.start()])
    tail = AGENT_TAIL_RE.match(low, m.end())
    return head.group() + m.group() + tail.group(), m.group("weak") is not None


def classify(raw: str, tokens: list[str]) -> dict[str, Any]:
    """Classify a raw user agent into a ua_map row; suspected_bot also flags desktop apps embedding Chromium (QtWebEngine, CEF), webviews without "; wv)", okhttp and a missing UA."""
    ua = unquote(raw[:UA_MAX_LEN])
    r = parse(ua).with_defaults()
    agent, weak = find_agent(ua.lower())
    token = match_token(ua, tokens)
    if r.device.family == "Spider" or (agent and not weak) or token:
        ua_class = "bot"
    else:
        ua_class = "suspected_bot" if weak or raw in ("", "-") or is_suspected_bot(ua) else "human"
    return {
        "cs_user_agent": raw,
        "family": r.user_agent.family,
        "os": r.os.family,
        "device": r.device.family,
        "ua_class": ua_class,
        "agent": agent,
        "robots_token": token,
    }


def parser_version(tokens: list[str]) -> str:
    """Version string from the ua-parser release, a hash of the tokens and the ua_class rule version."""
    digest = hashlib.sha256("\n".join(sorted(tokens)).encode()).hexdigest()[:8]
    return f"ua-parser-{version('ua-parser')}+builtins-{version('ua-parser-builtins')}+{digest}+{RULES_VERSION}"


def read_tokens(key: str) -> list[str] | None:
    """Read tokens from a robots_agents JSONL in S3; None if the key does not exist."""
    try:
        body = s3.get_object(Bucket=RESULTS_BUCKET, Key=key)["Body"].read().decode()
    except s3.exceptions.NoSuchKey:
        return None
    return [json.loads(line)["token"] for line in body.splitlines() if line.strip()]


def load_tokens() -> list[str]:
    """Tokens from the robots_agents JSONL in S3 (built by Terraform from robots_agents and robots_agents_url)."""
    tokens = read_tokens(ROBOTS_KEY) or []
    print(f"robots tokens: {len(tokens)}")
    return tokens


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
            settle = time.monotonic() + 15
            while time.monotonic() < settle:
                state = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
                if state in ("CANCELLED", "FAILED", "SUCCEEDED"):
                    break
                time.sleep(1)
            raise RuntimeError(f"Athena query {qid} stopped: Lambda out of time")
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def range_params(dt: str, lo: int, hi: int) -> list[str]:
    """Build Athena parameters for a dt and hour range."""
    return [f"'{dt.replace('-', '/')}'", str(lo), str(hi)]


def unload_params(dt: str, lo: int, hi: int) -> list[str]:
    """Build Athena parameters for the UNLOAD joins: map dt, raw dt, hour range."""
    return [f"'{dt}'", *range_params(dt, lo, hi)]


def list_keys(prefix: str) -> list[str]:
    """List all S3 keys under a prefix."""
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=RESULTS_BUCKET, Prefix=prefix)
    return [o["Key"] for p in pages for o in p.get("Contents", [])]


def delete_keys(keys: list[str]) -> None:
    """Delete S3 keys in batches of 1000."""
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        s3.delete_objects(Bucket=RESULTS_BUCKET, Delete={"Objects": batch, "Quiet": True})


def stream_results(qid: str) -> Iterator[list[str]]:
    """Yield the rows of a query result CSV from S3, without the header."""
    out = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["ResultConfiguration"]["OutputLocation"]
    u = urlparse(out)
    body = s3.get_object(Bucket=u.netloc, Key=u.path.lstrip("/"))["Body"]
    reader = csv.reader(codecs.getreader("utf-8")(body))
    next(reader, None)
    yield from reader


def write_ua_map(dt: str, lo: int, hi: int, tokens: list[str], deadline: float | None = None) -> int:
    """Write ua_map for the hour range, one object per hour; return the number of user agents."""
    qid = run_query(MAP_SQL, range_params(dt, lo, hi), deadline)
    pv = parser_version(tokens)
    rows: list[dict[str, Any]] = []
    done: set[int] = set()
    total = 0

    def flush(hour: int) -> None:
        nonlocal rows, total
        prefix = f"ua_map/dt={dt}/hour={hour:02d}/"
        key = f"{prefix}map.jsonl.gz"
        delete_keys([k for k in list_keys(prefix) if k != key])
        body = "".join(json.dumps(r) + "\n" for r in rows)
        s3.put_object(Bucket=RESULTS_BUCKET, Key=key, Body=gzip.compress(body.encode()))
        done.add(hour)
        total += len(rows)
        rows = []

    current: int | None = None
    for hour, ua in stream_results(qid):
        if current is not None and int(hour) != current:
            flush(current)
        current = int(hour)
        rows.append(classify(ua, tokens) | {"parser_version": pv})
    if total == 0 and current is None:
        raise RuntimeError(f"no user agents found for {dt} hours {lo}-{hi}")
    if current is not None:
        flush(current)
    delete_keys([k for h in range(lo, hi + 1) if h not in done for k in list_keys(f"ua_map/dt={dt}/hour={h:02d}/")])
    return total


def rebuild_summaries(dt: str, lo: int, hi: int, deadline: float | None = None) -> None:
    """UNLOAD the summary partitions for the range, then delete the files they replace; on failure delete the new files."""
    for table in SUMMARIES:
        prefixes = [f"summaries/{table}/dt={dt}/hour={h:02d}/" for h in range(lo, hi + 1)]
        old = {k for p in prefixes for k in list_keys(p)}
        location = f"s3://{RESULTS_BUCKET}/summaries/{table}/"
        try:
            run_query(UNLOAD_SQL[table].format(location=location), unload_params(dt, lo, hi), deadline)
        except Exception:
            delete_keys(sorted({k for p in prefixes for k in list_keys(p)} - old))
            raise
        delete_keys(sorted(old))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point."""
    tokens = load_tokens()
    print(f"tokens: {len(tokens)}")
    dt, lo, hi = parse_event(event, datetime.now(timezone.utc))
    deadline = time.monotonic() + context.get_remaining_time_in_millis() / 1000 - 20
    mapped = write_ua_map(dt, lo, hi, tokens, deadline)
    rebuild_summaries(dt, lo, hi, deadline)
    return {"dt": dt, "hours": [lo, hi], "user_agents": mapped}
