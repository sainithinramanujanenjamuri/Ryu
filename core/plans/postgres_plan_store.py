"""PostgreSQL Authoritative Durable Plan Store.

Provides production-grade durable persistence for Space Plans and TaskGraphs:
- Single-writer Compare-And-Swap (CAS) with PostgreSQL row-level locks (FOR UPDATE)
- Authoritative current Plan snapshot in 'plans' table per Space
- Append-only immutable historical snapshots in 'plan_history' table
- Deterministic serialization and deserialization (fails closed on malformed state)
- Space-scoped queries ensuring zero cross-space leakage (SCCA Law 1)
- Cold-boot reconstruction without depending on previous process memory

spec §16 (TaskGraph & PlanDelta), ADR-0003, ADR-0041, ADR-0045, REC-003, PLAN-DURABLE-001..010 — Phase 15.1
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg2
from psycopg2.extensions import connection
from ryu.pulse_bus.pulse import Pulse, Severity

from core.plans.delta import DeltaOp, PlanDelta
from core.plans.serialization import (
    deserialize_task_graph_json,
    serialize_task_graph_json,
)
from core.plans.task_graph import (
    IllegalStateTransitionError,
    TaskGraph,
    TaskNode,
    TaskNotFoundError,
)

logger = logging.getLogger(__name__)


class PulsePublisher(Protocol):
    def publish(self, pulse: Pulse) -> Pulse: ...


class PostgresPlanStore:
    """Production PostgreSQL-backed durable PlanStore.

    Authoritative for SpaceKernel plan state, plan CAS transitions,
    and cold-boot reconstruction.
    """

    def __init__(
        self,
        config: Any,
        bus: PulsePublisher | None = None,
        max_rebases: int = 3,
    ) -> None:
        self.config = config
        self.bus = bus
        self.max_rebases = max_rebases
        self._rebase_attempts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._ensure_schema()

    def _get_connection(self) -> connection:
        """Establish a direct PostgreSQL connection.

        Fails closed with psycopg2.OperationalError if database is unavailable.
        """
        host = getattr(self.config, "host", "localhost")
        port = int(getattr(self.config, "port", 5432))
        dbname = getattr(self.config, "db", None) or getattr(self.config, "dbname", "ryu_dev")
        user = getattr(self.config, "user", "ryu")
        password = getattr(self.config, "password", "ryu_dev_password")

        return psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
        )

    def _ensure_schema(self) -> None:
        """Ensure plans and plan_history tables exist.

        Fails closed if PostgreSQL is unreachable.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS plans (
            space_id            VARCHAR(255) PRIMARY KEY,
            plan_version        INTEGER      NOT NULL,
            graph_json          JSONB        NOT NULL,
            last_winning_delta  VARCHAR(255),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_plans_space ON plans (space_id);
        CREATE INDEX IF NOT EXISTS idx_plans_version ON plans (space_id, plan_version);

        CREATE TABLE IF NOT EXISTS plan_history (
            space_id            VARCHAR(255) NOT NULL,
            plan_version        INTEGER      NOT NULL,
            graph_json          JSONB        NOT NULL,
            delta_id            VARCHAR(255),
            committed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (space_id, plan_version)
        );
        CREATE INDEX IF NOT EXISTS idx_plan_history_space ON plan_history (space_id);
        CREATE INDEX IF NOT EXISTS idx_plan_history_committed ON plan_history (committed_at DESC);
        """
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
        finally:
            conn.close()

    def _clone_graph(self, graph: TaskGraph) -> TaskGraph:
        """Create an independent deep copy of a TaskGraph."""
        cloned_nodes = [
            TaskNode(
                id=n.id,
                capability=n.capability,
                params=dict(n.params),
                optional=n.optional,
                state=n.state,
                dependencies=list(n.dependencies),
                attempt=n.attempt,
                result_ref=n.result_ref,
                error=n.error,
            )
            for n in graph.nodes
        ]
        return TaskGraph(
            space_id=graph.space_id,
            plan_version=graph.plan_version,
            nodes=cloned_nodes,
        )

    def _apply_delta_ops(self, graph: TaskGraph, delta: PlanDelta) -> TaskGraph:
        """Apply PlanDelta operations to a clone of the graph."""
        new_graph = self._clone_graph(graph)

        for op in delta.ops:
            op_type: str = op.op if isinstance(op, DeltaOp) else str(op.get("op", ""))
            target_id: str = (
                op.target_node_id if isinstance(op, DeltaOp) else str(op.get("target_node_id", ""))
            )
            op_payload: dict[str, Any] = op.payload if isinstance(op, DeltaOp) else op

            if op_type == "add":
                existing = new_graph.get_node(target_id)
                if not existing:
                    new_node = TaskNode(
                        id=target_id,
                        capability=op_payload.get("capability", "default"),
                        params=dict(op_payload.get("params", {})),
                        optional=bool(op_payload.get("optional", False)),
                        dependencies=list(op_payload.get("dependencies", [])),
                        state=str(op_payload.get("state", "pending")),
                    )
                    new_graph.nodes.append(new_node)
            elif op_type == "remove":
                new_graph.nodes = [n for n in new_graph.nodes if n.id != target_id]
            elif op_type in ("reassign", "rollback"):
                target_node = new_graph.get_node(target_id)
                if target_node:
                    target_node.params.update(op_payload.get("params", {}))
            elif op_type == "transition":
                target_node = new_graph.get_node(target_id)
                if target_node is None:
                    raise TaskNotFoundError(
                        f"Task '{target_id}' not found in plan for space '{delta.space_id}'"
                    )
                from_state = op_payload.get("from_state") or op_payload.get("params", {}).get("from_state")
                if from_state and target_node.state.lower() != from_state.lower():
                    raise IllegalStateTransitionError(
                        f"Task '{target_id}' expected current state '{from_state}', "
                        f"but is '{target_node.state}'"
                    )
                to_state = op_payload.get("to_state") or op_payload.get("params", {}).get("to_state")
                if not to_state:
                    raise ValueError(f"Transition op requires 'to_state', got: {op}")
                target_node.transition_to(to_state)
                if "error" in op_payload:
                    target_node.error = op_payload["error"]
                if "result_ref" in op_payload:
                    target_node.result_ref = op_payload["result_ref"]
                if "attempt" in op_payload:
                    target_node.attempt = int(op_payload["attempt"])

        return new_graph

    def init_space_plan(self, space_id: str, nodes: list[TaskNode] | None = None) -> TaskGraph:
        """Initialize or retrieve the authoritative Space TaskGraph at plan_version 1.

        Idempotent: if plan already exists for this space, loads and returns current plan.
        """
        if not space_id or not space_id.strip():
            raise ValueError("space_id cannot be empty")

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT graph_json FROM plans WHERE space_id = %s;",
                        (space_id,),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        # Plan already exists in PostgreSQL — deserialize and return
                        data_str = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                        return deserialize_task_graph_json(data_str)

                    # Create initial plan v1
                    graph = TaskGraph(
                        space_id=space_id,
                        plan_version=1,
                        nodes=nodes or [],
                    )
                    graph.validate_dependencies()
                    graph_json = serialize_task_graph_json(graph)

                    cur.execute(
                        """
                        INSERT INTO plans (space_id, plan_version, graph_json, last_winning_delta, created_at, updated_at)
                        VALUES (%s, 1, %s, 'init', NOW(), NOW())
                        ON CONFLICT (space_id) DO NOTHING;
                        """,
                        (space_id, graph_json),
                    )

                    cur.execute(
                        """
                        INSERT INTO plan_history (space_id, plan_version, graph_json, delta_id, committed_at)
                        VALUES (%s, 1, %s, 'init', NOW())
                        ON CONFLICT (space_id, plan_version) DO NOTHING;
                        """,
                        (space_id, graph_json),
                    )

                    return graph
        finally:
            conn.close()

    def get_plan_version(self, space_id: str) -> int:
        """Return the current authoritative plan_version for a Space."""
        if not space_id or not space_id.strip():
            raise ValueError("space_id cannot be empty")

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT plan_version FROM plans WHERE space_id = %s;",
                        (space_id,),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        return int(row[0])
        finally:
            conn.close()

        # If not found, initialize and return 1
        graph = self.init_space_plan(space_id)
        return graph.plan_version

    def get_task_graph(self, space_id: str, version: int | None = None) -> TaskGraph:
        """Return the authoritative TaskGraph for a Space (current or historical)."""
        if not space_id or not space_id.strip():
            raise ValueError("space_id cannot be empty")

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    if version is None:
                        cur.execute(
                            "SELECT graph_json FROM plans WHERE space_id = %s;",
                            (space_id,),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            data_str = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                            return deserialize_task_graph_json(data_str)
                    else:
                        # Check historical snapshot first
                        cur.execute(
                            "SELECT graph_json FROM plan_history WHERE space_id = %s AND plan_version = %s;",
                            (space_id, version),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            data_str = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                            return deserialize_task_graph_json(data_str)

                        # Check current plan if it matches version
                        cur.execute(
                            "SELECT plan_version, graph_json FROM plans WHERE space_id = %s;",
                            (space_id,),
                        )
                        row = cur.fetchone()
                        if row is not None and int(row[0]) == version:
                            data_str = row[1] if isinstance(row[1], str) else json.dumps(row[1])
                            return deserialize_task_graph_json(data_str)

                        raise KeyError(f"Plan version {version} not found for space '{space_id}'")
        finally:
            conn.close()

        # If version is None and not found, initialize space plan
        return self.init_space_plan(space_id)

    def get_historical_graph(self, space_id: str, version: int) -> TaskGraph | None:
        """Return an immutable historical TaskGraph snapshot, or None if not found."""
        if not space_id or not space_id.strip():
            raise ValueError("space_id cannot be empty")

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT graph_json FROM plan_history WHERE space_id = %s AND plan_version = %s;",
                        (space_id, version),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        data_str = row[0] if isinstance(row[0], str) else json.dumps(row[0])
                        return deserialize_task_graph_json(data_str)
                    return None
        finally:
            conn.close()

    def commit_delta(
        self,
        delta: PlanDelta,
        proposal_id: str | None = None,
    ) -> tuple[bool, int, str | None]:
        """Attempt to commit a PlanDelta via Compare-And-Swap (CAS) in PostgreSQL.

        Enforces:
        - Row-level lock (SELECT ... FOR UPDATE) preventing concurrent race conditions
        - Transactional atomicity: plans update and plan_history insert commit together
        - Strict space isolation: all queries parameterized by delta.space_id
        - Deterministic pulse emission on commit or conflict

        Returns:
            (success: bool, current_version: int, winning_delta_id: str | None)
        """
        space_id = delta.space_id
        if not space_id or not space_id.strip():
            raise ValueError("space_id cannot be empty")

        cas_success = False
        current_ver = 0
        winning_id: str = "unknown"

        conn = self._get_connection()
        try:
            with conn:  # Transaction boundary
                with conn.cursor() as cur:
                    # Acquire row-level lock for this space's plan
                    cur.execute(
                        """
                        SELECT plan_version, graph_json, last_winning_delta
                        FROM plans
                        WHERE space_id = %s
                        FOR UPDATE;
                        """,
                        (space_id,),
                    )
                    row = cur.fetchone()

                    if row is None:
                        # Auto-initialize space plan at v1 if not exists
                        init_graph = TaskGraph(space_id=space_id, plan_version=1)
                        init_json = serialize_task_graph_json(init_graph)
                        cur.execute(
                            """
                            INSERT INTO plans (space_id, plan_version, graph_json, last_winning_delta, created_at, updated_at)
                            VALUES (%s, 1, %s, 'init', NOW(), NOW());
                            """,
                            (space_id, init_json),
                        )
                        cur.execute(
                            """
                            INSERT INTO plan_history (space_id, plan_version, graph_json, delta_id, committed_at)
                            VALUES (%s, 1, %s, 'init', NOW());
                            """,
                            (space_id, init_json),
                        )
                        current_ver = 1
                        current_graph = init_graph
                        winning_id = "init"
                    else:
                        current_ver = int(row[0])
                        data_str = row[1] if isinstance(row[1], str) else json.dumps(row[1])
                        current_graph = deserialize_task_graph_json(data_str)
                        winning_id = str(row[2]) if row[2] else "unknown"

                    # ─── CAS Evaluation ───────────────────────────────────────
                    if delta.base_version == current_ver:
                        # Ensure current version is recorded in history before advancement
                        cur.execute(
                            """
                            INSERT INTO plan_history (space_id, plan_version, graph_json, delta_id, committed_at)
                            VALUES (%s, %s, %s, %s, NOW())
                            ON CONFLICT (space_id, plan_version) DO NOTHING;
                            """,
                            (
                                space_id,
                                current_ver,
                                serialize_task_graph_json(current_graph),
                                winning_id,
                            ),
                        )

                        # Apply delta ops and validate
                        new_graph = self._apply_delta_ops(current_graph, delta)
                        new_graph.plan_version = delta.resulting_version
                        new_graph.validate_dependencies()
                        new_json = serialize_task_graph_json(new_graph)

                        # Atomically update plans table
                        cur.execute(
                            """
                            UPDATE plans
                            SET plan_version = %s,
                                graph_json = %s,
                                last_winning_delta = %s,
                                updated_at = NOW()
                            WHERE space_id = %s AND plan_version = %s;
                            """,
                            (
                                delta.resulting_version,
                                new_json,
                                delta.delta_id,
                                space_id,
                                current_ver,
                            ),
                        )

                        if cur.rowcount == 1:
                            # Append immutable historical record of resulting_version
                            cur.execute(
                                """
                                INSERT INTO plan_history (space_id, plan_version, graph_json, delta_id, committed_at)
                                VALUES (%s, %s, %s, %s, NOW());
                                """,
                                (
                                    space_id,
                                    delta.resulting_version,
                                    new_json,
                                    delta.delta_id,
                                ),
                            )
                            cas_success = True
                            current_ver = delta.resulting_version
                            winning_id = delta.delta_id
        finally:
            conn.close()

        # ─── Post-Transaction Notifications & Observability ───────────────────
        if cas_success:
            with self._lock:
                if proposal_id:
                    self._rebase_attempts.pop((space_id, proposal_id), None)

            if self.bus is not None:
                serializable_ops = [
                    {"op": o.op, "target_node_id": o.target_node_id, **o.payload}
                    if isinstance(o, DeltaOp)
                    else o
                    for o in delta.ops
                ]
                self.bus.publish(
                    Pulse(
                        id=f"plan-delta-{space_id}-{delta.resulting_version}",
                        space_id=space_id,
                        type="plan.delta",
                        severity=Severity.INFO,
                        source="plan_store",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "base_version": delta.base_version,
                            "resulting_version": delta.resulting_version,
                            "ops": serializable_ops,
                        },
                        taint=False,
                        correlation_id=f"corr-plan-{space_id}",
                        parent_pulse_id=None,
                    )
                )
            return True, current_ver, None

        # CAS FAILURE: Stale base_version
        if self.bus is not None:
            self.bus.publish(
                Pulse(
                    id=f"plan-superseded-{space_id}-{delta.base_version}-{delta.delta_id}",
                    space_id=space_id,
                    type="plan.version.superseded",
                    severity=Severity.WARNING,
                    source="plan_store",
                    timestamp=datetime.now(timezone.utc),
                    payload={
                        "superseded_version": delta.base_version,
                        "current_version": current_ver,
                        "winning_delta_id": winning_id,
                    },
                    taint=False,
                    correlation_id=f"corr-plan-{space_id}",
                    parent_pulse_id=None,
                )
            )

        # Livelock check
        if proposal_id:
            with self._lock:
                key = (space_id, proposal_id)
                attempts = self._rebase_attempts.get(key, 0) + 1
                self._rebase_attempts[key] = attempts

            if attempts >= self.max_rebases and self.bus is not None:
                self.bus.publish(
                    Pulse(
                        id=f"livelock-failed-{space_id}-{proposal_id}",
                        space_id=space_id,
                        type="task.failed",
                        severity=Severity.ERROR,
                        source="plan_store",
                        timestamp=datetime.now(timezone.utc),
                        payload={
                            "task_id": proposal_id,
                            "error_class": "terminal.plan_livelock",
                            "message": (
                                f"Plan CAS livelock: exceeded maximum automatic rebase "
                                f"attempts ({self.max_rebases})"
                            ),
                            "plan_version": current_ver,
                        },
                        taint=False,
                        correlation_id=f"corr-plan-{space_id}",
                        parent_pulse_id=None,
                    )
                )

        return False, current_ver, winning_id

    def list_active_spaces(self) -> list[str]:
        """Return all space IDs having persisted plans in PostgreSQL."""
        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT space_id FROM plans ORDER BY space_id ASC;")
                    return [str(row[0]) for row in cur.fetchall()]
        finally:
            conn.close()

    def load_all_plans(self) -> dict[str, TaskGraph]:
        """Load all active Space TaskGraphs from PostgreSQL.

        Used during cold-boot recovery to reconstruct all active Spaces.
        """
        conn = self._get_connection()
        plans: dict[str, TaskGraph] = {}
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT space_id, graph_json FROM plans;")
                    for row in cur.fetchall():
                        sid = str(row[0])
                        data_str = row[1] if isinstance(row[1], str) else json.dumps(row[1])
                        plans[sid] = deserialize_task_graph_json(data_str)
            return plans
        finally:
            conn.close()

    def restore_graph(self, graph: TaskGraph, winning_delta: str | None = None) -> None:
        """Persist a reconstructed or checkpointed TaskGraph to PostgreSQL."""
        graph.validate_dependencies()
        graph_json = serialize_task_graph_json(graph)
        delta_id = winning_delta or f"restore-v{graph.plan_version}"

        conn = self._get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO plans (space_id, plan_version, graph_json, last_winning_delta, updated_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (space_id) DO UPDATE SET
                            plan_version = EXCLUDED.plan_version,
                            graph_json = EXCLUDED.graph_json,
                            last_winning_delta = EXCLUDED.last_winning_delta,
                            updated_at = NOW();
                        """,
                        (graph.space_id, graph.plan_version, graph_json, delta_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO plan_history (space_id, plan_version, graph_json, delta_id, committed_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (space_id, plan_version) DO UPDATE SET
                            graph_json = EXCLUDED.graph_json,
                            delta_id = EXCLUDED.delta_id;
                        """,
                        (graph.space_id, graph.plan_version, graph_json, delta_id),
                    )
        finally:
            conn.close()
