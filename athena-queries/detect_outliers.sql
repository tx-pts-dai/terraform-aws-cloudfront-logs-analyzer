-- 0 Define parameters based on your needs
-- -- default params will query the whole day of Feb 1, 2026 and 
-- -- look for IPs exceeding 1000 requests in any rolling 5-min window
WITH params AS (
  SELECT
    '2026/02/01' AS from_dt,
    '2026/02/01' AS to_dt,
    0            AS from_hour,
    23           AS to_hour,
    1000      AS threshold_requests,
    "CHANGE"  AS distribution_id
),

-- 1 Preprocess timestamp and filter out whitelisted IPs
base AS (
  SELECT l.c_ip,
    from_unixtime(CAST(l.timestamp AS BIGINT)) AS ts  -- use timestamp_ms for millisecond precision
  FROM cloudfront_logs_parquet l
  CROSS JOIN params p
  WHERE l.dt BETWEEN p.from_dt AND p.to_dt
    AND l.hour BETWEEN p.from_hour AND p.to_hour
    AND l.distribution_id = p.distribution_id -- filter for specific distribution if needed
    AND l.timestamp IS NOT NULL       -- needed in case parquet file doesn't have this field populated
    -- filter out IPs in whitelist
    AND NOT EXISTS (
      SELECT 1 FROM ip_whitelist w
      WHERE contains(w.ip, CAST(l.c_ip AS IPADDRESS)) -- only works with Athena Engine 3 (enforced in workgroup settings)
    )
),

-- 2 Assign each request to a 30-second window
-- window_start does not work in GROUP BY as an alias
-- -- we create a temp table "buckets" to overcome this
buckets AS (
  SELECT
    c_ip,
    date_trunc('second', ts)
      - INTERVAL '1' SECOND * (CAST(second(ts) AS INTEGER) % 30) AS window_start
  FROM base
),

-- 3 Count requests per IP per 30-second window
thirty_sec_windows AS (
  SELECT
    c_ip,
    window_start,
    COUNT(*) AS requests_in_30sec
  FROM buckets
  GROUP BY c_ip, window_start
),

five_min_rolling AS (
  SELECT 
    c_ip,
    window_start,
    SUM(requests_in_30sec) OVER (
      PARTITION BY c_ip 
      ORDER BY window_start 
      ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING
    ) AS requests_in_5min
  FROM thirty_sec_windows
),

outliers AS (
  SELECT 
    c_ip,
    MAX(requests_in_5min) AS max_requests_5min,
    COUNT(DISTINCT window_start) AS windows_exceeded--,
    --ARRAY_AGG(DISTINCT window_start) FILTER (WHERE requests_in_5min > p.threshold_requests) AS violation_times
  FROM five_min_rolling
  CROSS JOIN params p
  WHERE requests_in_5min > p.threshold_requests
  GROUP BY c_ip
),

enhanched_outliers AS (
  SELECT 
    o.*,
    COALESCE(geo.country, 'unknown') AS country,
    COALESCE(geo.region, 'unknown') AS region,
    COALESCE(geo.city, 'unknown') AS city,
    COALESCE(geo.org, 'unknown') AS organization
  FROM outliers o
  LEFT JOIN ip_geolocation geo
    ON o.c_ip = geo.ip
)

SELECT * FROM enhanched_outliers;
