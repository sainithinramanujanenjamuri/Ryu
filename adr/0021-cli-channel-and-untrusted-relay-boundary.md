# ADR-0021: CLI Channel and Untrusted Relay Boundary

## Context
Phase 8 introduces the first external human-facing communication channel: the Command Line Interface (`channels/cli/`). In automated environments, CI/CD pipelines, or compromised shells, input may be piped or redirected from external untrusted processes into the CLI, attempting to spoof interactive human approvals.

## Decision
1. **Interactive TTY Enforcement:**
   - Direct user input from a real interactive terminal (`isatty() == True`) is treated as authentic human input (`taint: false`).
   - Any input provided via non-interactive standard input (pipes, file redirection, automated shell wrappers) is treated as an untrusted external relay and strictly tagged as tainted (`taint: true`).
2. **Untrusted Relay Boundary:**
   - Automated scripts cannot bypass human gate intent by piping canned responses without triggering taint inheritance.
   - Piped tainted input cannot clear existing security taints or approve high-risk security grants without explicit verification and human clearance.
3. **Fail-Closed Presentation:**
   - When an approval request is evaluated via CLI, all execution context, risk tier, requester identity, plan version, and taint status are rendered prominently prior to capturing the human decision.

## Alternatives Considered
- *Treating all stdin equally:* Rejected because malicious background processes could pipe "APPROVE\n" into an unattended CLI session, bypassing human gate intent.
- *Blocking all non-interactive stdin:* Rejected because legitimate batch testing and automation workflows require scripted interaction when properly identified and audited.

## Consequences
- Clean boundary between interactive human presence and automated relays.
- Taint status is preserved across CLI boundaries (Law 4, Law 6).

## Date
2026-09-20

