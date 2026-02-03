# Converting to Parquet

Small utility to convert JSONL files (one JSON object per line) to Parquet with sensible typing.

Quick start

```bash
cd scripts && \
  python -m venv .venv && \
  source .venv/bin/activate && \
  pip install -r requirements.txt
```



Basic usage

- Convert a single JSONL to a Parquet file:
```bash
python jsonl_to_parquet.py \
  -i sample-ip-geolocation.jsonl \
  -o ip-geolocation.parquet
```

- Convert multiple inputs into separate Parquet files in a directory:
```bash
python jsonl_to_parquet.py \
  -i sample-ip4-whitelist.jsonl sample-ip-geolocation.jsonl \
  -o "./"
```

- Combine multiple inputs into a single Parquet file:
```bash
python jsonl_to_parquet.py \
  -i sample-ip4-whitelist.jsonl sample-ip6-whitelist.jsonl \
  -o combined-ip-whitelist.parquet
```

Options
- `--parse-dates`: attempt to parse columns containing `date`/`time` into timestamps.
