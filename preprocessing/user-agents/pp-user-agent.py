"""
Lambda function: CloudFront Parquet → Preprocessed User-Agent Parquet

Triggered by S3 events (directly or via SQS/SNS fan-out).
For each incoming Parquet file it:
  1. Downloads the file from the source bucket.
  2. Normalises column names to lowercase (CloudFront uses mixed-case, e.g. cs_User_Agent).
  3. URL-decodes user-agent strings before parsing/hashing (raw CF logs are URL-encoded).
  4. Parses each *unique* UA once, then maps results back to all rows.
  5. Writes a new Parquet file (SNAPPY) containing only the preprocessed_user_agents schema.
  6. Uploads to OUTPUT_BUCKET under:
       {OUTPUT_PREFIX}/{distributionid}/{dt}/{hour}/{original_filename}
     matching the Glue partition projection for the preprocessed_user_agents table.

Required environment variables:
  OUTPUT_BUCKET   – destination S3 bucket name
  OUTPUT_PREFIX   – key prefix inside OUTPUT_BUCKET (default: "preprocessed-data")
  INPUT_PREFIX    – key prefix to strip from the source key to reach the partition path
                    (e.g. "cf-logs/" so the remainder is {distributionid}/{yyyy}/{MM}/{dd}/{hh}/file)
"""

import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.parse

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from ua_parser import parse

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "preprocessed-data")
INPUT_PREFIX  = os.environ.get("INPUT_PREFIX", "")

# Schema must match the user_agents_schema defined in glue.tf
OUTPUT_SCHEMA = pa.schema([
  pa.field("timestamp",      pa.string()),
  pa.field("timestamp_ms",   pa.string()),
  pa.field("sc_status",      pa.string()),
  pa.field("user_agent_md5", pa.string()),
  pa.field("ua_family",      pa.string()),
  pa.field("ua_major",       pa.string()),
  pa.field("ua_minor",       pa.string()),
  pa.field("ua_patch",       pa.string()),
  pa.field("ua_patch_minor", pa.string()),
  pa.field("os_family",      pa.string()),
  pa.field("os_major",       pa.string()),
  pa.field("os_minor",       pa.string()),
  pa.field("os_patch",       pa.string()),
  pa.field("os_patch_minor", pa.string()),
  pa.field("device_family",  pa.string()),
  pa.field("device_brand",   pa.string()),
  pa.field("device_model",   pa.string()),
])

_EMPTY_UA_FIELDS: dict[str, str] = {
  "ua_family": "", "ua_major": "", "ua_minor": "", "ua_patch": "", "ua_patch_minor": "",
  "os_family": "", "os_major": "", "os_minor": "", "os_patch": "", "os_patch_minor": "",
  "device_family": "", "device_brand": "", "device_model": "",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(val) -> str:
  """Return val as a string, or '' when None."""
  return val if val is not None else ""


def _parse_ua(raw_ua: str) -> dict[str, str]:
  """URL-decode and parse a user-agent string into component fields."""
  if not raw_ua or raw_ua == "-":
    return _EMPTY_UA_FIELDS.copy()
  ua_str = urllib.parse.unquote_plus(raw_ua)
  try:
    r = parse(ua_str)
    ua  = r.user_agent
    os_ = r.os
    dev = r.device
    return {
      "ua_family":      _s(ua.family      if ua  else None),
      "ua_major":       _s(ua.major       if ua  else None),
      "ua_minor":       _s(ua.minor       if ua  else None),
      "ua_patch":       _s(ua.patch       if ua  else None),
      "ua_patch_minor": _s(ua.patch_minor if ua  else None),
      "os_family":      _s(os_.family     if os_ else None),
      "os_major":       _s(os_.major      if os_ else None),
      "os_minor":       _s(os_.minor      if os_ else None),
      "os_patch":       _s(os_.patch      if os_ else None),
      "os_patch_minor": _s(os_.patch_minor if os_ else None),
      "device_family":  _s(dev.family     if dev else None),
      "device_brand":   _s(dev.brand      if dev else None),
      "device_model":   _s(dev.model      if dev else None),
    }
  except Exception as exc:
    logger.warning("UA parse failed for %r: %s", raw_ua[:120], exc)
    return _EMPTY_UA_FIELDS.copy()


def _ua_md5(raw_ua: str) -> str:
  """MD5 of the raw (URL-encoded) UA string as CloudFront writes it.

  No lower() needed: CloudFront encoding is deterministic, so the same UA
  always arrives identically encoded. The Athena join is simply:
    md5(cs_user_agent) = user_agent_md5
  """
  if not raw_ua or raw_ua == "-":
    return ""
  return hashlib.md5(raw_ua.encode("utf-8")).hexdigest()


def _parse_s3_key(key: str) -> tuple[str, str, str, str]:
  """
  Extract (distributionid, dt, hour, filename) from an S3 object key.

  After stripping INPUT_PREFIX the remainder must look like:
    {distributionid}/{year}/{month}/{day}/{hour}/{filename}.parquet
  e.g. E2O0SLRWP5N8D1/2026/02/04/10/E2O0SLRWP5N8D1.2026-02-04-10.abc123.parquet

  Returns:
    distributionid  e.g. "E2O0SLRWP5N8D1"
    dt              e.g. "2026/02/04"
    hour            e.g. "10"
    filename        e.g. "E2O0SLRWP5N8D1.2026-02-04-10.abc123.parquet"
  """
  stripped = key
  prefix = INPUT_PREFIX.rstrip("/")
  if prefix and stripped.startswith(prefix + "/"):
      stripped = stripped[len(prefix) + 1:]

  parts = stripped.split("/")
  if len(parts) < 6:
    raise ValueError(
      f"S3 key {key!r} does not match expected structure "
      f"(expected ≥6 path segments after stripping INPUT_PREFIX={INPUT_PREFIX!r}, got {len(parts)})"
    )

  distributionid = parts[0]
  dt             = f"{parts[1]}/{parts[2]}/{parts[3]}"
  hour           = parts[4]
  # preserve any extra nesting
  filename       = "/".join(parts[5:])

  return distributionid, dt, hour, filename


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process_file(src_bucket: str, src_key: str) -> None:
  distributionid, dt, hour, filename = _parse_s3_key(src_key)

  # Use temporary files for input and output to avoid memory issues with large files.
  with tempfile.NamedTemporaryFile(suffix=".parquet", dir="/tmp", delete=False) as f:
    tmp_in = f.name
  with tempfile.NamedTemporaryFile(suffix=".parquet", dir="/tmp", delete=False) as f:
    tmp_out = f.name

  t_start = time.perf_counter()

  try:
    logger.info("Downloading s3://%s/%s", src_bucket, src_key)
    t0 = time.perf_counter()
    s3_client.download_file(src_bucket, src_key, tmp_in)
    logger.info("[timing] download:       %.3fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    table = pq.read_table(tmp_in)

    # Build a case-insensitive column lookup, then select only needed columns.
    col_map = {c.lower(): c for c in table.schema.names}
    needed  = ["timestamp", "timestamp_ms", "sc_status", "cs_user_agent"]
    table   = table.select([col_map[c] for c in needed])
    table   = table.rename_columns(needed)
    df = table.to_pandas()
    logger.info("[timing] read+select:    %.3fs  (%d rows)", time.perf_counter() - t0, len(df))

    input_row_count = len(df)

    # Null UAs become "-" so they map to _EMPTY_UA_FIELDS and don't break the dict lookup.
    df["cs_user_agent"] = df["cs_user_agent"].fillna("-")

    # Parse each unique UA once to avoid redundant work (high row-count, lower UA cardinality).
    # map() is row-preserving: every input row gets exactly one output row at the same index.
    unique_uas = df["cs_user_agent"].unique()
    logger.info("Parsing %d unique user agents across %d rows", len(unique_uas), input_row_count)

    t0 = time.perf_counter()
    parsed_map = {ua: _parse_ua(ua) for ua in unique_uas}
    md5_map    = {ua: _ua_md5(ua)   for ua in unique_uas}
    logger.info("[timing] ua parse+md5:   %.3fs  (%d unique UAs)", time.perf_counter() - t0, len(unique_uas))

    t0 = time.perf_counter()
    df["user_agent_md5"] = df["cs_user_agent"].map(md5_map)
    for field in (
      "ua_family", "ua_major", "ua_minor", "ua_patch", "ua_patch_minor",
      "os_family", "os_major", "os_minor", "os_patch", "os_patch_minor",
      "device_family", "device_brand", "device_model",
    ):
      df[field] = df["cs_user_agent"].map(lambda ua, f=field: parsed_map[ua][f])

    df = df.drop(columns=["cs_user_agent"])
    logger.info("[timing] df enrichment:  %.3fs", time.perf_counter() - t0)

    # Ensure no rows were lost/added during UA enrichment.
    assert len(df) == input_row_count, (
        f"Row count mismatch after UA enrichment: expected {input_row_count}, got {len(df)}"
    )

    # Coerce everything to string; replace pandas NaN with empty string
    t0 = time.perf_counter()
    for col in df.columns:
      df[col] = df[col].fillna("").astype(str)

    out_table = pa.Table.from_pandas(df, schema=OUTPUT_SCHEMA, preserve_index=False)
    pq.write_table(out_table, tmp_out, compression="snappy")
    logger.info("[timing] write parquet:  %.3fs", time.perf_counter() - t0)

    out_key = f"{OUTPUT_PREFIX.rstrip('/')}/{distributionid}/{dt}/{hour}/{filename}"
    t0 = time.perf_counter()
    logger.info("Uploading %d rows → s3://%s/%s", len(df), OUTPUT_BUCKET, out_key)
    s3_client.upload_file(tmp_out, OUTPUT_BUCKET, out_key)
    logger.info("[timing] upload:         %.3fs", time.perf_counter() - t0)

    logger.info("[timing] total:          %.3fs", time.perf_counter() - t_start)

  finally:
    for path in (tmp_in, tmp_out):
      try:
        os.remove(path)
      except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event, context):
  """
  Supports three invocation shapes:
    1. Direct S3 event        → event["Records"][*]["s3"]
    2. SQS-wrapped S3 event   → event["Records"][*]["body"] contains S3 event JSON
    3. SQS-wrapped SNS+S3     → event["Records"][*]["body"] is SNS notification whose
                                  "Message" field contains the S3 event JSON
  """
  records = event.get("Records", [])
  logger.info("Received %d record(s)", len(records))

  for record in records:
    # SNS notification wrapping S3 event
    if record.get("eventSource") == "aws:sqs":
      body = json.loads(record["body"])
      # SNS fan-out wraps the S3 event inside the SNS "Message" field
      if "Message" in body:
        s3_records = json.loads(body["Message"]).get("Records", [])
      else:
        s3_records = body.get("Records", [])
    # Direct S3 event
    else:
        s3_records = [record]

    for s3_record in s3_records:
      src_bucket = s3_record["s3"]["bucket"]["name"]
      logger.debug("Processing record for bucket %r: %r", src_bucket, s3_record)
      src_key    = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])
      logger.debug("Extracted key: %r", src_key)
      _process_file(src_bucket, src_key)

  return {"statusCode": 200, "processed": len(records)}
