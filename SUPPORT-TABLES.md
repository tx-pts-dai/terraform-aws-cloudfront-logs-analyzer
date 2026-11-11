# Support Tables Data Management

## Overview

Two support tables are created to optimize CloudFront log analysis:

1. **`ip_whitelist`** - Filter out known good IPs (search bots, monitoring, internal services)
2. **`ip_geolocation`** - Cache geolocation lookups to avoid repeated API calls

Both tables use **Parquet format** stored in S3 for optimal query performance and cost efficiency.

---

## Table: ip_whitelist

**Purpose:** Define allowed IPs that should be excluded from outlier detection or have higher rate limits.

**Schema:**
```sql
ip             STRING   -- IP address to whitelist
ip_version     STRING   -- 4 or 6
cidr_size      STRING   -- Size of the IP block
reason         STRING   -- Why this IP is whitelisted
added_date     STRING   -- When added
added_by       STRING   -- Who added it
request_limit  STRING   -- (optional) Max requests per 5-min window
```

**Location:** `s3://<some-bucket>/<some-prefix>/`

**Format:** Parquet

**Sample Data:** See [scripts/sample-ip4-whitelist.jsonl](scripts/sample-ip4-whitelist.jsonl) (convert to Parquet before uploading)

---

## Table: ip_geolocation

**Purpose:** Cache IP geolocation data:
- Avoid API rate limits
- Reduce costs
- Speed up repeated queries
- Not do require external HTTP calls

**Schema:**
```sql
ip           STRING     -- IP address
ip_version   STRING     -- 4 or 6
city         STRING     -- City name
region       STRING     -- State/region
country      STRING     -- Country name
hostname     STRING     -- Reverse DNS
org          STRING     -- Organization/ISP with ASN
loc          STRING     -- "latitude,longitude"
cached_date  STRING     -- When cached
source       STRING     -- Source of the lookup (e.g., ipinfo)
```

**Location:** `s3://<some-bucket>/<some-prefix>/`

**Format:** Parquet

**Sample Data:** See [scripts/sample-ip-geolocation.jsonl](scripts/sample-ip-geolocation.jsonl) (convert to Parquet before uploading)

---

## Table: robots_agents

**Purpose:** Explicit robots.txt agent tokens used by the UA summarizer to set `robots_token` in `ua_map`. Only created with `summaries.enable = true`.

**Schema:**
```sql
token     STRING   -- Agent token, e.g. GPTBot
operator  STRING   -- Company behind the agent
purpose   STRING   -- search, ai-training, ai-search, ai-user, ads or other
notes     STRING   -- Free text
```

**Location:** `s3://<results-bucket>/supporters/robots-agents/robots_agents.json`

**Format:** JSON lines

**Maintenance:** Written by Terraform, do not edit in S3. Source is `summaries.robots_agents` (default list of known bots). If `summaries.robots_agents_url` is set, Terraform fetches it at plan/apply and adds its `User-agent` tokens (explicit wins case-insensitively; `*`, comments, short and generic tokens ignored) with `purpose = other` and `notes = robots_agents_url`. See [README](README.md#ua-summaries).
