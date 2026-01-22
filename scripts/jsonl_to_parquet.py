#!/usr/bin/env python3
"""Convert a JSONL file to Parquet with sensible typing for whitelist data.

Usage:
  python scripts/jsonl_to_parquet.py -i scripts/example-ip-whitelist.jsonl -o support/ip-whitelist.parquet

Writes Snappy-compressed Parquet. Requires pandas and pyarrow.
"""
import argparse
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
def convert_one(in_path: Path, out_path: Path, parse_dates: bool = False):
    """Read a single JSONL and write a Parquet file, keeping all fields as strings by default.

    Nulls are preserved. If `parse_dates` is True, date-like columns are parsed and formatted
    as ISO-8601 strings.
    """
    df = pd.read_json(in_path, lines=True)

    # Optionally parse date-like columns, but still output as strings (ISO format)
    if parse_dates:
        for col in df.columns:
            if isinstance(col, str) and ("date" in col.lower() or "time" in col.lower()):
                try:
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    df[col] = parsed.where(parsed.isna(), parsed.dt.strftime("%Y-%m-%dT%H:%M:%S%z"))
                except Exception:
                    df[col] = df[col].astype(str)

    # Convert all columns to strings, preserving nulls
    for col in df.columns:
        df[col] = df[col].where(df[col].isna(), df[col].astype(str))

    table = pa.Table.from_pandas(df, preserve_index=False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path.as_posix(), compression="snappy")


def convert(inputs: list[Path], out_path: Path, parse_dates: bool = False):
    """Convert one or more JSONL inputs to Parquet.

    - Single input -> writes to `out_path` file.
    - Multiple inputs + `out_path` is a directory -> writes one Parquet per input.
    - Multiple inputs + `out_path` is a file -> concatenates and writes a single Parquet file.
    """
    if len(inputs) == 1:
        convert_one(inputs[0], out_path, parse_dates=parse_dates)
        return

    # multiple inputs
    if out_path.exists() and out_path.is_dir() or str(out_path).endswith("/"):
        out_dir = out_path if out_path.is_dir() else out_path
        out_dir.mkdir(parents=True, exist_ok=True)
        for inp in inputs:
            out_file = out_dir / (inp.stem + ".parquet")
            convert_one(inp, out_file, parse_dates=parse_dates)
        return

    # concatenate inputs into single DataFrame and write
    dfs = [pd.read_json(p, lines=True) for p in inputs]
    df = pd.concat(dfs, ignore_index=True)

    if parse_dates:
        for col in df.columns:
            if isinstance(col, str) and ("date" in col.lower() or "time" in col.lower()):
                try:
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    df[col] = parsed.where(parsed.isna(), parsed.dt.strftime("%Y-%m-%dT%H:%M:%S%z"))
                except Exception:
                    df[col] = df[col].astype(str)

    for col in df.columns:
        df[col] = df[col].where(df[col].isna(), df[col].astype(str))

    table = pa.Table.from_pandas(df, preserve_index=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path.as_posix(), compression="snappy")
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].astype(str)
    table = pa.Table.from_pandas(df, preserve_index=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path.as_posix(), compression="snappy")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--input", type=Path, nargs='+', required=True,
                   help="One or more input JSONL files")
    p.add_argument("-o", "--output", type=Path, required=True,
                   help="Output Parquet file or directory")
    p.add_argument("--parse-dates", action="store_true", help="Parse date/time-like columns to ISO strings")
    args = p.parse_args()

    inputs = args.input if isinstance(args.input, list) else [args.input]
    convert(inputs, args.output, parse_dates=args.parse_dates)
    print("Wrote:", args.output)
