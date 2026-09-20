"""Unit tests for CLI argument parsing, subcommands, and exit codes.

spec §2, §4, ROADMAP Phase 8, CLI-001 through CLI-008 — Phase 8
"""

import io
import json
import pytest

from channels.cli import (
    CLIContext,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    EXIT_SYNTAX_ERROR,
    main,
)
from channels.approval.client import ApprovalClient
from channels.approval.auth import ApproverAuthenticator, InMemoryCredentialStore
from core.space.approver import ApprovalManager, InMemoryApprovalStore


@pytest.fixture
def cli_ctx():
    cred_store = InMemoryCredentialStore()
    nonce_store = cred_store
    secret_store = {"secret://approver/alice-key": "secret-alice-123"}
    auth = ApproverAuthenticator(cred_store, nonce_store, secret_store)
    mgr = ApprovalManager(store=InMemoryApprovalStore())
    client = ApprovalClient(mgr, auth)

    out = io.StringIO()
    err = io.StringIO()
    ctx = CLIContext(
        approval_client=client,
        out_stream=out,
        err_stream=err,
    )
    return ctx, out, err, client


def test_cli_syntax_error_returns_exit_code_2():
    out = io.StringIO()
    err = io.StringIO()
    ctx = CLIContext(out_stream=out, err_stream=err)

    code = main(["unknown_subcommand"], ctx=ctx)
    assert code == EXIT_SYNTAX_ERROR

    code = main(["approval", "approve"], ctx=ctx)  # Missing required arguments
    assert code == EXIT_SYNTAX_ERROR


def test_cli_status_text_and_json(cli_ctx):
    ctx, out, err, _ = cli_ctx

    code = main(["status"], ctx=ctx)
    assert code == EXIT_SUCCESS
    output = out.getvalue()
    assert "RYU AI Runtime Status" in output
    assert "ONLINE" in output

    # JSON mode
    out.seek(0)
    out.truncate(0)
    code = main(["status", "--json"], ctx=ctx)
    assert code == EXIT_SUCCESS
    data = json.loads(out.getvalue())
    assert data["status"] == "online"
    assert "spaces" in data


def test_cli_space_subcommands(cli_ctx):
    ctx, out, err, _ = cli_ctx

    # space list
    code = main(["space", "list"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "SPACE ID" in out.getvalue()

    # space inspect
    out.seek(0)
    out.truncate(0)
    code = main(["space", "inspect", "space_test_1"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "space_test_1" in out.getvalue()

    # space list --json
    out.seek(0)
    out.truncate(0)
    code = main(["space", "list", "--json"], ctx=ctx)
    assert code == EXIT_SUCCESS
    data = json.loads(out.getvalue())
    assert isinstance(data, list)


def test_cli_task_subcommands(cli_ctx):
    ctx, out, err, _ = cli_ctx

    # task list
    code = main(["task", "list"], ctx=ctx)
    assert code == EXIT_SUCCESS

    # task inspect
    out.seek(0)
    out.truncate(0)
    code = main(["task", "inspect", "task-123"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "task-123" in out.getvalue()


def test_cli_audit_subcommands(cli_ctx):
    ctx, out, err, _ = cli_ctx

    code = main(["audit", "stream"], ctx=ctx)
    assert code == EXIT_SUCCESS


def test_cli_approval_inspect_not_found(cli_ctx):
    ctx, out, err, _ = cli_ctx

    code = main(["approval", "inspect", "nonexistent-id"], ctx=ctx)
    assert code == EXIT_GENERAL_ERROR
    assert "not found" in err.getvalue().lower()


def test_cli_approval_list(cli_ctx):
    ctx, out, err, client = cli_ctx
    client.request_approval(
        request_id="app-test-1",
        space_id="space-1",
        capability="fs.read",
        summary="Test approval 1",
    )

    code = main(["approval", "list", "--space-id", "space-1"], ctx=ctx)
    assert code == EXIT_SUCCESS
    assert "app-test-1" in out.getvalue()

    out.seek(0)
    out.truncate(0)
    code = main(["approval", "list", "--space-id", "space-1", "--json"], ctx=ctx)
    assert code == EXIT_SUCCESS
    data = json.loads(out.getvalue())
    assert len(data) == 1
    assert data[0]["request_id"] == "app-test-1"

