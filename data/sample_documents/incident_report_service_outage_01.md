# Title: Incident Report — Payment Service Outage (Synthetic)

Source: Synthetic incident report derived from public post-mortem patterns
Date: 2026-02-10

## Incident Summary

A payment processing service experienced a 4-hour outage between 14:00 and 18:00 UTC. Approximately 38,000 customer transactions were unable to complete during the window. No data was lost. Service was fully restored by 18:12 UTC.

## Timeline

- 14:03 UTC — Automated monitoring detected elevated error rates on the payment gateway API (>40% 5xx responses).
- 14:11 UTC — On-call engineer paged. Initial investigation identified database connection pool exhaustion on the primary payment database cluster.
- 14:45 UTC — Root cause identified: a routine database maintenance job that was scheduled during off-peak hours ran during peak traffic due to a misconfigured cron schedule after a recent deployment.
- 15:30 UTC — Maintenance job terminated and database connections recovered. Traffic routing restored to primary cluster.
- 18:00 UTC — Full service recovery confirmed with error rates returning to baseline.

## Root Cause

A deployment on 2026-02-09 included a configuration change to the scheduled maintenance job. The cron expression was incorrectly set, causing the job to run at 14:00 UTC (peak traffic) instead of 02:00 UTC (off-peak). The change was not reviewed in the deployment checklist.

## Impact

- 4 hours of degraded service affecting payment processing
- 38,000 failed transactions (all retried successfully by customers or automated retry logic)
- No data integrity issues
- No security impact

## Corrective Actions Taken

- Maintenance job schedule corrected and verified
- Deployment checklist updated to require explicit review of any cron schedule changes
- Runbook updated to include database connection pool recovery steps
- Monitoring alert tuned to detect connection pool saturation earlier

This document represents an operational incident report. Core signals:
- service outage with defined impact window
- root cause identified
- corrective actions documented
- no data loss, no security implications
