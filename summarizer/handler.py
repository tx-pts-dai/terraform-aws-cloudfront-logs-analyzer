import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from typing import Any
from urllib.parse import unquote

import boto3
from ua_parser import parse

DATABASE = os.environ.get("DATABASE", "")
WORKGROUP = os.environ.get("WORKGROUP", "")
OUTPUT_LOCATION = os.environ.get("OUTPUT_LOCATION", "")
RESULTS_BUCKET = os.environ.get("RESULTS_BUCKET", "")
ROBOTS_KEY = os.environ.get("ROBOTS_KEY", "")

UA_DIM_PREFIX = "ua_dim/"
SUMMARIES = ("cf_ua_hourly", "cf_bot_errors_hourly")

AGENT_RE = re.compile(
    r"([a-z0-9_.-]*(bot|crawler|spider|headless|lightpanda|thinkers|magentanews|python|curl|wget"
    r"|go-http|okhttp|java|axios|node-fetch|scrapy)[a-z0-9_.-]*)"
)

NEW_UAS_SQL = """
SELECT r.cs_user_agent, min(r.hour) AS first_hour
FROM cloudfront_logs_parquet r
LEFT JOIN ua_dim d ON r.cs_user_agent = d.cs_user_agent
WHERE r.dt = ? AND r.hour BETWEEN ? AND ?
  AND r.cs_user_agent IS NOT NULL AND d.cs_user_agent IS NULL
GROUP BY r.cs_user_agent
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
    low = ua.lower()
    hits = [t for t in tokens if t.lower() in low]
    return max(hits, key=len) if hits else ""


def classify(raw: str, tokens: list[str]) -> dict[str, Any]:
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
    digest = hashlib.sha256("\n".join(sorted(tokens)).encode()).hexdigest()[:8]
    return f"ua-parser-{version('ua-parser')}+{digest}"


def load_tokens() -> list[str]:
    body = s3.get_object(Bucket=RESULTS_BUCKET, Key=ROBOTS_KEY)["Body"].read().decode()
    return [json.loads(line)["token"] for line in body.splitlines() if line.strip()]


def run_query(sql: str, params: list[str] | None = None) -> str:
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
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def fetch_rows(qid: str) -> list[list[str]]:
    rows: list[list[str]] = []
    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
    for page in pages:
        rows += [[c.get("VarCharValue", "") for c in r["Data"]] for r in page["ResultSet"]["Rows"]]
    return rows[1:]


def range_params(dt: str, lo: int, hi: int) -> list[str]:
    return [f"'{dt.replace('-', '/')}'", str(lo), str(hi)]


def list_keys(prefix: str) -> list[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=RESULTS_BUCKET, Prefix=prefix)
    return [o["Key"] for p in pages for o in p.get("Contents", [])]


def delete_keys(keys: list[str]) -> None:
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        s3.delete_objects(Bucket=RESULTS_BUCKET, Delete={"Objects": batch, "Quiet": True})


def write_ua_dim(rows: list[dict[str, Any]], name: str) -> str:
    key = f"{UA_DIM_PREFIX}{name}.jsonl"
    body = "".join(json.dumps(r) + "\n" for r in rows)
    s3.put_object(Bucket=RESULTS_BUCKET, Key=key, Body=body.encode())
    return key


def add_new_uas(dt: str, lo: int, hi: int, tokens: list[str]) -> int:
    rows = fetch_rows(run_query(NEW_UAS_SQL, range_params(dt, lo, hi)))
    pv = parser_version(tokens)
    out = [
        classify(raw, tokens) | {"parser_version": pv, "first_seen": f"{dt} {int(h):02d}"}
        for raw, h in rows
    ]
    if out:
        write_ua_dim(out, f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}")
    return len(out)


def reparse(tokens: list[str]) -> int:
    old_keys = list_keys(UA_DIM_PREFIX)
    pv = parser_version(tokens)
    out: list[dict[str, Any]] = []
    for key in old_keys:
        body = s3.get_object(Bucket=RESULTS_BUCKET, Key=key)["Body"].read().decode()
        for line in body.splitlines():
            if line.strip():
                old = json.loads(line)
                out.append(classify(old["cs_user_agent"], tokens) | {"parser_version": pv, "first_seen": old["first_seen"]})
    if out:
        new_key = write_ua_dim(out, f"reparse-{uuid.uuid4().hex[:8]}")
        delete_keys([k for k in old_keys if k != new_key])
    return len(out)


def rebuild_summaries(dt: str, lo: int, hi: int) -> None:
    for table in SUMMARIES:
        delete_keys(
            [k for h in range(lo, hi + 1) for k in list_keys(f"summaries/{table}/dt={dt}/hour={h:02d}/")]
        )
        location = f"s3://{RESULTS_BUCKET}/summaries/{table}/"
        run_query(UNLOAD_SQL[table].format(location=location), range_params(dt, lo, hi))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    tokens = load_tokens()
    target = parse_event(event, datetime.now(timezone.utc))
    if target is None:
        return {"reparsed": reparse(tokens)}
    dt, lo, hi = target
    added = add_new_uas(dt, lo, hi, tokens)
    rebuild_summaries(dt, lo, hi)
    return {"dt": dt, "hours": [lo, hi], "new_uas": added}
