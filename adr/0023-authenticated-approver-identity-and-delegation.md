# ADR-0023: Authenticated Approver Identity and Delegation

## Context
In multi-agent systems, unauthenticated approvals allow rogue agents or external processes to approve high-risk operations. Every human gate must resolve to a single deterministic authenticated `approver_id` bound to cryptographic credentials.

## Decision
1. **Wire Protocol `token-hmac-v1`:**
   - Pre-image specification: 9 newline-delimited fields (`token-hmac-v1`, `approver_id`, `timestamp`, `nonce`, `space_id`, `approval_id`, `decision`, `plan_version`, `capability_request_hash`).
   - Constant-time HMAC-SHA256 signature verification (`hmac.compare_digest`).
   - Clock-skew window strictly bounded to $\pm 60$ seconds.
   - 32-character hex nonce recorded in durable store to guarantee replay protection.
2. **SecretStore / SecretRef Boundary:**
   - Raw token secrets are never stored in PostgreSQL or log files.
   - The PostgreSQL `approver_credentials` table stores only `secret_ref` URIs (e.g. `secret://approver/alice-key`).
   - Secret key material is resolved fail-closed via the local `SecretStore`.
3. **Deterministic Space Mapping:**
   - Each Space designates an authoritative `approver_id`.
   - Submissions from approvers other than the designated approver for that Space are rejected (`PermissionError`).
4. **Credential Lifecycle:**
   - Revoked or expired credentials are rejected before nonce consumption or signature verification.

## Alternatives Considered
- *Cleartext token comparison:* Rejected because timing attacks and network sniffing expose secrets.
- *Storing plaintext token hashes:* Rejected because HMAC verification requires the secret key material at verification time to avoid offline dictionary attacks.

## Consequences
- Cryptographically verifiable human identity for all approval decisions.
- Nonce persistence prevents replay attacks across crashes and network retries.

## Date
2026-09-20

