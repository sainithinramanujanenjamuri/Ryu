# ADR 0002: Transport vs Record Authority

## Status
Accepted

## Context
Phase 1 introduces the Durable Pulse Bus with two systems: PostgreSQL for persistence and Redis Streams for transport.
We must definitively establish the relationship and authority between them to answer OPEN-001 and OPEN-002 from the contract matrix.

## Decision

1. **Authoritative System:** PostgreSQL is ALWAYS the authoritative system of record.
2. **Durably Accepted:** A Pulse is durably accepted the moment the PostgreSQL transaction commits successfully.
3. **Published:** A Pulse is considered "published" to the transport when the Redis XADD succeeds, which only happens *after* the Postgres commit.
4. **Transport Failure (Redis Outage):** If Postgres commit succeeds but Redis XADD fails, the error is observable (logged/raised, never silent), but the Pulse IS durable and will not be lost.
5. **Transport Guarantee:** Redis delivery is *at-least-once*.
6. **Consumer Idempotency:** The consumer is responsible for handling duplicate deliveries using the `Pulse.id` as the idempotency key.
7. **Storage Idempotency:** PostgreSQL `INSERT` uses `ON CONFLICT DO NOTHING` keyed on `Pulse.id`. A duplicate append returns the existing position.
8. **Replay/Reconciliation:** Startup scan and eventual reconciliation find Pulses in Postgres where `redis_published = FALSE` and re-publish them to Redis.
9. **Process Crash:** If a process crashes between persistence (Postgres) and publication (Redis), startup reconciliation handles it.
10. **Not Provided:** We explicitly DO NOT provide *exactly-once* delivery.

## Consequences
- The Pulse Bus guarantees durable persistence over transport availability.
- Consumers must be idempotent.
- Recovery from a transport failure is automatic on process restart or periodic reconciliation.

