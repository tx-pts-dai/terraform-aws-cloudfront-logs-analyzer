"""
Local test runner for pp-user-agent.py.

Usage (from this directory, with the .venv activated):
    python test_local.py

No AWS credentials or extra dependencies required — boto3 S3 calls are
patched to use the local sample Parquet file.
"""

import json
import logging
import os
import shutil
import sys
import unittest.mock

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the sample file
# ---------------------------------------------------------------------------
HERE        = Path(__file__).parent
SAMPLE_FILE = HERE / "E2O0SLRWP5N8D1.2026-03-12-10.cb7f44ce.parquet"

if not SAMPLE_FILE.exists():
    sys.exit(f"Sample file not found: {SAMPLE_FILE}")

# ---------------------------------------------------------------------------
# Environment variables the Lambda reads at import time
# ---------------------------------------------------------------------------
OUTPUT_DIR = HERE / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

os.environ.setdefault("OUTPUT_BUCKET", "local-test-bucket")
os.environ.setdefault("OUTPUT_PREFIX", "preprocessed-data")
# S3 key for the sample will be: cf-logs/E2O0SLRWP5N8D1/2026/02/04/10/E2O0SLRWP5N8D1.2026-02-04-10.fae09c75.parquet
os.environ.setdefault("INPUT_PREFIX",  "cf-logs")

# ---------------------------------------------------------------------------
# Craft a minimal S3 event that matches the sample file name
# ---------------------------------------------------------------------------
S3_KEY = "cf-logs/E2O0SLRWP5N8D1/2026/03/12/10/E2O0SLRWP5N8D1.2026-03-12-10.cb7f44ce.parquet"

FAKE_S3_EVENT = {
    "Records": [
        {
            "eventSource": "aws:s3",
            "s3": {
                "bucket": {"name": "source-bucket"},
                "object": {"key": S3_KEY},
            },
        }
    ]
}

# The same event wrapped in SQS (tests the SQS branch of the handler)
FAKE_SQS_EVENT = {
    "Records": [
        {
            "eventSource": "aws:sqs",
            "body": json.dumps(FAKE_S3_EVENT),
        }
    ]
}

# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def _fake_download(bucket, key, dest_path):
    """Copy the local sample file instead of hitting S3."""
    print(f"  [mock] download_file s3://{bucket}/{key} → {dest_path}")
    shutil.copy(str(SAMPLE_FILE), dest_path)


def _fake_upload(src_path, bucket, key):
    """Write the output Parquet to OUTPUT_DIR instead of S3."""
    out = Path(OUTPUT_DIR) / Path(key).name
    shutil.copy(src_path, str(out))
    print(f"  [mock] upload_file {src_path} → s3://{bucket}/{key}")
    print(f"         saved locally at: {out}")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(mod, event, label):
    print(f"\n=== {label} ===")

    with (
        unittest.mock.patch.object(mod.s3_client, "download_file", side_effect=_fake_download),
        unittest.mock.patch.object(mod.s3_client, "upload_file",   side_effect=_fake_upload),
    ):
        result = mod.handler(event, context=None)

    print(f"  handler returned: {result}")

    # Verify output
    import pyarrow.parquet as pq
    outputs = list(Path(OUTPUT_DIR).glob("*.parquet"))
    if not outputs:
        print("  ERROR: no output file was written")
        return

    out_table = pq.read_table(str(outputs[-1]))
    print(f"  output rows    : {out_table.num_rows}")
    print(f"  output columns : {out_table.schema.names}")

    # Row count must match input
    in_table = pq.read_table(str(SAMPLE_FILE))
    assert out_table.num_rows == in_table.num_rows, (
        f"Row count mismatch: input={in_table.num_rows} output={out_table.num_rows}"
    )
    print(f"  row count check: OK ({out_table.num_rows} rows)")

    # Spot-check a few parsed values
    df = out_table.to_pandas()
    print("\n  Sample output (first 3 rows):")
    print(df[["timestamp", "sc_status", "user_agent_md5", "ua_family", "os_family", "device_family"]].head(3).to_string(index=False))
    print()


if __name__ == "__main__":
    # Rename module file to importable name (hyphen → underscore handled via import alias)
    sys.path.insert(0, str(HERE))

    # Python can't import a module whose filename contains hyphens; use importlib directly
    import importlib.util
    spec = importlib.util.spec_from_file_location("pp_user_agent", HERE / "pp-user-agent.py")
    import types
    pp_user_agent = types.ModuleType("pp_user_agent")
    spec.loader.exec_module(pp_user_agent)
    sys.modules["pp_user_agent"] = pp_user_agent

    run(pp_user_agent, FAKE_S3_EVENT,  "Direct S3 event")
    run(pp_user_agent, FAKE_SQS_EVENT, "SQS-wrapped S3 event")
    print("All checks passed.")
