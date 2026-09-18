# Bus Recovery Runbook

## Overview
Recovers from Postgres vs Redis state skew (Transport vs Record skew).
According to ADR-0002, Postgres is the authoritative truth. Redis stream acts as the distribution layer.

## Symptoms
- Pulse consumer claims they missed a pulse.
- Logs show Redis publish failure, but PostgreSQL commit succeeded.

## Detection
Run the following SQL on Postgres:
```sql
SELECT count(*) FROM pulses WHERE redis_published = FALSE;
```
If count > 0, there are unpublished pulses.

## Reconciliation Steps
The `DurablePulseBus` has a `reconcile_unpublished(limit)` method.
Normally, it's called on process startup or by a periodic background reconciler.
To manually reconcile:
1. Connect to Ryu REPL or Python env
2. `bus.reconcile_unpublished(limit=1000)`
3. Monitor logs for Redis connection.

## Verification
```sql
SELECT count(*) FROM pulses WHERE redis_published = FALSE;
```
Should return 0.
Consumers should log successful ingestion of the delayed events.

