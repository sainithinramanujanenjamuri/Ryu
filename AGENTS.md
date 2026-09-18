# RYU AI — Agent Engineering Instructions

**Project:** RYU AI
**Architecture:** Space-Centric Cognitive Architecture (SCCA)
**Repository state:** Clean rebuild
**Current phase:** Phase 0 — Repository Scaffold
**Architecture status:** Frozen

---

## 1. Purpose

This file defines the mandatory engineering rules for any AI agent or human contributor modifying the RYU AI repository.

These rules apply to:

* coding agents
* code-generation agents
* autonomous development agents
* human contributors
* CI automation
* future sub-agents operating inside the repository

The purpose is to ensure that implementation remains faithful to the frozen architecture, machine-readable contracts, executable harness, and roadmap gates.

**When this file conflicts with an implementation shortcut, the implementation shortcut loses.**

---

# 2. Required Reading Before Coding

Before making architectural or implementation changes, read:

1. `docs/architecture.md`
2. `docs/CONTRACT_MATRIX.md`
3. `ROADMAP.md`
4. this file
5. relevant ADRs under `adr/`
6. relevant machine-readable contracts under `contracts/registry/`
7. relevant harness cases under `harness/cases/`

Do not begin implementation based only on a summary, previous conversation, generated plan, or README.

The repository files are the source of truth.

---

# 3. Architecture Is Frozen

RYU AI uses:

```text
Space-Centric Cognitive Architecture (SCCA)
```

The primary execution hierarchy is:

```text
Runtime
  ↓
Spaces
  ↓
Execution Plans
  ↓
Teams
  ↓
Agents
  ↓
Workers
  ↓
Skills
  ↓
Memory
  ↓
Insights
```

The six architectural laws are:

### Law 1 — Everything Happens Inside a Space

Every meaningful operation belongs to a Space.

Space is the primary authority and isolation boundary.

Nothing may silently operate outside a Space.

### Law 2 — Capabilities Are Requested, Never Owned

Agents, Workers, and other components do not inherently own capabilities.

Capabilities are requested and admitted through the defined authorization path.

### Law 3 — Components Communicate Through Pulses

Components communicate through typed Pulses and defined contracts.

Do not introduce arbitrary hidden communication paths between architectural components.

### Law 4 — Knowledge Belongs to the Space First

Knowledge is Space-local by default.

Promotion outside the Space requires the defined promotion mechanism and authorization.

### Law 5 — Humans Define Goals; Ryu Organizes Execution

Human intent defines goals.

RYU organizes planning, assignment, execution, monitoring, adaptation, and escalation according to the architecture.

The system must not silently redefine human goals.

### Law 6 — Failures Are Contained, Escalated, and Never Silent

Failures must remain within their originating scope and be surfaced to the appropriate authority.

```text
Tool failure
    ↓
Worker knows
    ↓
Agent knows
    ↓
Space Orchestrator knows
    ↓
Space decides
retry / reassign / escalate
```

Silently swallowed failures are defects.

---

# 4. Dependency Direction

The dependency direction is strictly one-way.

The deterministic core must remain independent from higher-level components.

In particular:

```text
core/
```

MUST NOT import from:

```text
agents/
workers/
skills/
workflows/
```

This boundary is mandatory.

It must be enforced by automated dependency checks.

Forbidden examples:

```python
from agents import ...
from workers import ...
from skills import ...
from workflows import ...
```

inside `core/`.

Do not bypass this using:

* dynamic imports
* runtime import tricks
* `sys.path` manipulation
* hidden plugin imports
* circular dependency hacks
* conditional imports intended to evade dependency checks

If core requires an abstraction implemented by a higher layer, define the abstraction at the correct lower boundary.

---

# 5. Contracts Before Code

Contracts are authoritative.

The machine-readable contract registry lives under:

```text
contracts/registry/
```

Contract changes must happen before implementation changes that consume them.

The intended flow is:

```text
Architecture
    ↓
Contract Matrix
    ↓
Machine-readable Contract
    ↓
Code Generation
    ↓
Implementation
    ↓
Harness
    ↓
Evidence
```

Do not duplicate contract definitions manually across:

* Python
* Rust
* tests
* documentation
* harness fixtures

Generated artifacts must come from the registry where specified.

---

# 6. Contract Registry

The contract registry contains machine-readable definitions for system behavior.

Relevant areas include:

```text
contracts/registry/
├── pulse-types.json
├── payload-schemas/
├── failure-taxonomy.json
├── capability-risks.json
└── grants.json
```

Do not invent a second registry.

Do not silently modify a contract to make an implementation pass.

If the implementation and contract disagree:

1. verify the architecture;
2. verify the Contract Matrix;
3. determine whether the implementation is wrong;
4. if the architecture itself cannot express the requirement, create an ADR.

---

# 7. Harness Is the Executable Specification

The harness is not a collection of decorative tests.

A feature is considered proven only when:

```text
Harness Case
    +
spec_map.yaml mapping
    +
Architecture acceptance criterion
    =
Verified feature
```

A directory existing does not prove a feature.

A class importing does not prove a feature.

A test containing `assert True` does not prove a feature.

A README claim does not prove a feature.

Coverage percentage does not prove architectural correctness.

---

# 8. No Fake Tests

Never create tests such as:

```python
def test_future_feature():
    assert True
```

Never write tests that mock away the behavior they are supposed to verify.

Never make a test pass by weakening the assertion.

Never convert an expected failure into a success merely to make CI green.

For future functionality that is not yet implemented:

```python
pytest.skip(...)
```

may be used when explicitly required by the roadmap.

The skip must identify the relevant future phase/specification.

---

# 9. Phase Discipline

The roadmap defines the implementation order.

Do not skip phases merely because a later feature appears useful.

The required broad order is:

```text
Frozen Architecture
        ↓
Contracts
        ↓
Repository Scaffold
        ↓
Pulse Bus
        ↓
Executable Harness
        ↓
Space Kernel
        ↓
Resource Manager
        ↓
Orchestrator
        ↓
First Real Agent
        ↓
Workers / Sandbox
        ↓
Node Runtime
        ↓
Human Channels
        ↓
Skills / MCP
        ↓
Memory / Adaptation
        ↓
Multi-Platform Nodes
        ↓
v1.0
```

The current repository is being rebuilt from the beginning.

Previous implementation claims, milestone counts, old coverage numbers, or historical code must not be treated as current evidence.

---

# 10. Current Phase — Phase 0

The current phase is:

```text
Phase 0 — Repository Scaffold
```

Phase 0 establishes:

* repository structure
* Python workspace
* Rust workspace
* contracts structure
* code-generation structure
* harness structure
* CI
* dependency guard
* documentation
* ADR structure
* project memory
* minimal initial Pulse Bus behavior required by the roadmap

Phase 0 must remain boring.

---

# 11. Phase 0 Restrictions

During Phase 0, do NOT implement:

* real LLM calls
* Ollama integration
* OpenAI integration
* Agent reasoning
* autonomous agents
* Space Kernel behavior
* Admission Control
* production Resource Manager
* Workers
* sandbox execution
* browser automation
* shell execution
* Node Runtime behavior
* MCP
* Memory adapters
* vector databases
* knowledge promotion
* learning
* autonomous research
* autonomous coding
* voice runtime
* self-modifying goals

Future modules may exist as structural stubs where required.

They must not pretend to work.

---

# 12. Stubs

When a future component requires a callable interface, use a typed stub.

Example:

```python
def execute(...):
    raise NotImplementedError("spec §X.Y — Phase N")
```

The stub must:

* have a correct type signature
* have useful documentation
* identify the relevant specification
* identify the intended phase
* avoid fake behavior

Do not implement partial behavior and label it complete.

---

# 13. Pulse Rules

Pulses are typed contracts.

A Pulse must have the fields and semantics defined by the contract registry.

At minimum, the implementation must respect:

```text
type
payload
pulse_id
parent_pulse_id
correlation_id
severity
taint
```

where applicable according to the contract.

Unknown Pulse types must be rejected.

Malformed payloads must be rejected.

Validation must happen before append.

Rejected Pulses must not silently enter the event stream.

---

# 14. Pulse Rejection

Use the defined rejection mechanism:

```text
PulseRejectedError
```

It must expose the contract-defined information including:

```text
reason
offending_type
details
```

Do not replace typed rejection with:

```text
return None
```

or:

```text
False
```

or silent logging.

---

# 15. No Silent Failures

Every failure must have an explicit outcome.

Examples include:

* permission denial
* budget denial
* malformed input
* invalid contract
* timeout
* rate limiting
* resource conflict
* terminal failure
* transient failure
* approval timeout
* node disconnection
* sandbox rejection

If the architecture defines a Pulse for the condition, emit the Pulse.

If the architecture defines a synchronous response, return it.

If the architecture requires escalation, escalate.

Never swallow an exception merely to keep the system running.

---

# 16. Replay Is First-Class

Replay is a core architectural requirement.

System behavior must eventually be reconstructable from persisted:

```text
Pulses
+
state
+
recorded LLM calls
```

Do not design systems that depend on invisible mutable state that cannot be reconstructed.

If a bug cannot be reproduced because required causal information was not persisted, treat that as an observability defect.

---

# 17. State and Event Consistency

When implementing state-changing behavior, consider:

```text
State Mutation
        +
Pulse Publication
```

as an atomicity concern.

Do not silently create a situation where:

```text
state changed
but event disappeared
```

or:

```text
event exists
but state mutation never happened
```

If the architecture does not yet specify the required transaction/recovery semantics, do not invent them casually.

Record the ambiguity and use an ADR when required.

---

# 18. Space Isolation

Space is a hard boundary.

Information and authority must not leak between Spaces.

This applies to:

* memory
* artifacts
* plans
* agents
* workers
* resources
* subscriptions
* grants
* credentials
* Pulses
* execution context
* audit information

Cross-Space operations must follow the explicit architecture-defined path.

Never use global mutable state as a shortcut.

---

# 19. Secrets

Secrets must be represented using the defined mechanism.

Do not place resolved credentials directly into:

* Pulse payloads
* Handoff Notes
* failure messages
* logs
* traces
* memory
* audit records
* prompts

Secret resolution belongs at the latest possible point inside the authorized execution boundary.

Never print secrets for debugging.

Never add a test fixture containing a real credential.

---

# 20. Taint

Taint is security-relevant state.

Treat externally sourced or untrusted content according to the architecture's taint model.

Do not convert:

```text
tainted
```

into:

```text
trusted
```

merely because a component has processed it.

Taint clearance must follow the defined authorization mechanism.

The clearance itself must remain auditable.

---

# 21. Capabilities

Capabilities are requested through the defined capability path.

Do not allow components to bypass:

```text
CapabilityRequest
        ↓
Admission / Authorization
        ↓
Execution
```

No direct hidden execution path should exist for:

* shell
* filesystem
* browser
* network
* screen
* external nodes
* GPU
* device capabilities

A convenience API must not become an authorization bypass.

---

# 22. Node Runtime

The Node Runtime is a separate trust boundary.

The Node Runtime contract is platform-independent.

The current first physical-device target is:

```text
Windows host
```

The current Linux development environment is:

```text
Ubuntu 22.04.5 LTS
GNU/Linux 6.6.87.2-microsoft-standard-WSL2
x86_64
```

WSL2 may be used for Linux development and complementary validation.

WSL2 must NOT be described as native Linux physical hardware.

Platform-specific implementation belongs behind the Node Runtime platform boundary.

Future platforms may include:

```text
Linux
Windows
macOS
Android
iOS
Raspberry Pi
```

Do not implement future platform behavior before its roadmap phase.

---

# 23. LLM Boundary

The deterministic core must not depend on an LLM.

Phases 0–4 are intentionally deterministic.

The first real LLM enters in Phase 5.

The intended boundary is:

```text
Deterministic orchestration
        ↓
Agent state machine
        ↓
LLM transition function
```

The LLM must not become a hidden dependency of:

* contracts
* Pulse validation
* Space isolation
* admission control
* core runtime invariants
* replay infrastructure
* security enforcement

---

# 24. Orchestrator

The Orchestrator must remain thin.

The architecture defines the cognitive/control decomposition.

Do not move business logic into:

```text
orchestrator.py
```

merely because it is convenient.

Logic belongs in the appropriate module:

```text
Goal Analyzer
Planner
Team Builder
Monitor / Reconciler
Adapter / Reflector
```

or the exact decomposition defined by the current frozen architecture and applicable ADRs.

If there is a discrepancy between roadmap wording and frozen architecture, stop and resolve it through the documented ADR process rather than silently choosing one.

---

# 25. Agents

Agents are not the foundation of RYU AI.

They are introduced only at the roadmap-defined phase.

An Agent is a state machine operating through defined Pulse subscriptions and contracts.

Do not create an Agent that directly bypasses:

* Space authority
* capability admission
* Pulse communication
* plan versioning
* resource management
* security controls

---

# 26. Workers

Workers execute capabilities under the defined execution boundary.

Workers must not invent their own failure taxonomy.

Worker failures must map to the system failure taxonomy.

Worker inputs must remain data.

Untrusted output must not silently become executable instructions.

---

# 27. External Systems

External systems include:

* MCP servers
* network services
* external nodes
* third-party skills
* external tools
* providers

Treat them as untrusted boundaries unless the architecture explicitly establishes trust.

Do not add an external integration merely because it makes development easier.

Do not introduce cloud AI APIs unless the architecture and roadmap explicitly require them.

---

# 28. Dependencies

Prefer minimal dependencies.

Before adding a dependency, ask:

1. Is it required by the architecture?
2. Is it required by the current roadmap phase?
3. Does it weaken the core boundary?
4. Does it introduce a new trust surface?
5. Can the requirement be implemented using the existing stack?
6. Does it require an ADR?

Do not add libraries "for future use."

---

# 29. Security Over Convenience

Never weaken:

* authorization
* isolation
* taint
* secret handling
* grant validation
* auditability
* replayability
* failure visibility

to make a test pass.

If a security test is inconvenient, fix the implementation.

Do not weaken the test.

---

# 30. Testing Requirements

Every meaningful implementation change must have appropriate tests.

Use multiple levels where applicable:

```text
Unit
Integration
Harness
Security
Chaos
Replay
```

The relevant level is determined by the contract and roadmap gate.

Do not confuse:

```text
unit-tested
```

with:

```text
architecture verified
```

Evidence must match the actual verification level.

---

# 31. Evidence Status

Use these evidence concepts consistently:

```text
SPECIFIED
CONTRACTED
IMPLEMENTED
UNIT_VERIFIED
INTEGRATION_VERIFIED
CHAOS_VERIFIED
SECURITY_VERIFIED
GATE_VERIFIED
```

Do not claim a higher evidence level than the tests actually demonstrate.

For example:

```text
Implemented != Security Verified
```

and:

```text
Unit Verified != Phase Complete
```

---

# 32. Definition of Done

A change is not complete unless applicable requirements are satisfied.

For each implementation change:

* [ ] Relevant spec section identified
* [ ] Contract checked
* [ ] Implementation boundary respected
* [ ] Relevant harness case added/updated
* [ ] `harness/spec_map.yaml` updated
* [ ] Tests pass
* [ ] Dependency guard passes
* [ ] Contract/codegen synchronization passes
* [ ] No silent failure path introduced
* [ ] ADR created if the specification did not already answer the decision

---

# 33. Architecture Changes

Do NOT edit the frozen architecture casually.

Architecture changes are permitted only when:

1. a harness requirement cannot be expressed by the current specification, or
2. implemented code proves that the frozen specification is inconsistent.

When an architecture change is required:

```text
Identify mismatch
      ↓
Create ADR
      ↓
Document alternatives
      ↓
Document affected contracts
      ↓
Update architecture if justified
      ↓
Update Contract Matrix
      ↓
Update roadmap
      ↓
Update harness/spec_map
      ↓
Implement
```

Never perform a "quick documentation fix" to hide an implementation mismatch.

---

# 34. ADR Discipline

Architectural decisions belong under:

```text
adr/
```

Naming:

```text
adr/NNNN-short-title.md
```

Each ADR should contain:

* context
* problem
* decision
* alternatives considered
* alternatives rejected
* affected specification sections
* consequences
* date

ADRs are append-only.

Do not rewrite historical decisions to make them appear as though they were always correct.

---

# 35. Git Discipline

Keep commits focused.

Prefer commits such as:

```text
feat(contracts): add pulse registry
feat(harness): add causation contract case
feat(pulse-bus): implement registry validation
test(space): add isolation harness
docs(adr): record node platform decision
```

Do not combine unrelated architectural changes into one commit.

Do not commit generated artifacts without verifying their generation process.

Do not commit:

* secrets
* credentials
* `.env` contents containing real secrets
* temporary debug dumps
* generated junk
* local machine state

---

# 36. Environment Safety

Never assume a particular machine path.

Never hardcode:

```text
D:\...
C:\Users\...
/home/<user>/...
```

unless explicitly required by a test.

The project must remain portable.

Environment-specific behavior belongs in configuration.

---

# 37. Before Editing

Before modifying code:

```text
1. Read the relevant specification.
2. Find the relevant contract.
3. Find the relevant roadmap deliverable.
4. Find or create the relevant harness case.
5. Identify dependency direction.
6. Check existing implementation.
7. Make the smallest correct change.
8. Run the relevant tests.
```

Do not start by rewriting large portions of the repository.

---

# 38. Working Loop

Use this loop for every task:

```text
READ
 ↓
UNDERSTAND
 ↓
LOCATE CONTRACT
 ↓
LOCATE SPEC
 ↓
LOCATE HARNESS
 ↓
IMPLEMENT
 ↓
TEST
 ↓
REPLAY / SECURITY CHECK where applicable
 ↓
UPDATE SPEC MAP
 ↓
UPDATE MEMORY / ADR if required
 ↓
REPORT EVIDENCE
```

---

# 39. When Something Fails

Do not immediately rewrite the architecture.

First determine:

```text
Is the contract wrong?
Is the implementation wrong?
Is the test wrong?
Is the environment wrong?
Is the specification ambiguous?
```

Then act accordingly.

If the specification is insufficient:

```text
STOP
DOCUMENT
ADR
```

Do not invent architecture through code.

---

# 40. Agent Behavior

Coding agents must:

* inspect before editing
* preserve existing valid work
* avoid speculative implementation
* avoid unnecessary refactoring
* avoid hidden behavior
* report actual results
* distinguish implemented from verified
* stop at the requested roadmap phase

Coding agents must NOT:

* continue to the next phase automatically
* claim completion without evidence
* replace real tests with mocks
* weaken security controls
* bypass contracts
* bypass the Space boundary
* introduce LLM calls prematurely
* silently modify architecture
* hide failures

---

# 41. Phase Completion

A phase is complete only when its roadmap exit gate is satisfied.

The following are NOT sufficient:

```text
"All files created."
"Code compiles."
"Tests import."
"Coverage is high."
"Demo runs."
"Agent says complete."
```

The roadmap gate is the authority.

---

# 42. Current Bootstrap Rule

For the current repository bootstrap:

```text
CREATE STRUCTURE
        ↓
CREATE FOUNDATIONAL TOOLING
        ↓
CREATE REQUIRED CONTRACT STRUCTURE
        ↓
CREATE PHASE 0 HARNESS
        ↓
IMPLEMENT ONLY REQUIRED PHASE 0 LOGIC
        ↓
VERIFY
        ↓
STOP
```

Do not continue into Phase 1 automatically.

---

# 43. Final Reporting Standard

Every substantial coding task must report:

```text
What changed
What was verified
What was not implemented
Tests executed
Tests passed
Tests skipped
Tests failed
Relevant spec sections
Relevant harness cases
ADR created, if any
Remaining blockers
```

Never report:

```text
"Everything works"
```

without evidence.

Prefer:

```text
Phase 0 harness: 3/3 PASS
Future harness cases: SKIPPED
Rust workspace: cargo check PASS
Dependency guard: PASS
Phase 0 exit gate: PASS
```

---

# 44. Governing Principle

The fundamental engineering rule for RYU AI is:

> **If an architectural requirement cannot be traced from specification → contract → implementation boundary → executable evidence, it is not proven.**

Build slowly.

Build deterministically.

Make boundaries enforceable.

Make failures visible.

Make behavior replayable.

Do not confuse code volume with progress.

**The repository must earn every claim it makes.**
