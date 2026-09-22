"""Unit tests for Interactive Developer CLI Shell (banner, autocompletion, un-echoed token prompts).

spec §2, §4, ADR-0026, CONTRACT CLI-009, CLI-010, CLI-011 — Phase 8.5
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
import pytest

from channels.approval.auth import ApproverAuthenticator, InMemoryCredentialStore
from channels.approval.client import ApprovalClient
from channels.cli.context import CLIContext
from channels.cli.main import main
from channels.cli.shell import RyuInteractiveShell
from core.space.attention import AttentionBudget
from core.space.approver import ApprovalManager, InMemoryApprovalStore


@pytest.fixture
def shell_setup():
    out = io.StringIO()
    err = io.StringIO()
    in_s = io.StringIO()

    store = InMemoryApprovalStore()
    budget = AttentionBudget(default_limit=4)
    manager = ApprovalManager(store=store)
    # attach attention budget to manager for tests
    manager.attention_budget = budget  # type: ignore

    cred_store = InMemoryCredentialStore()
    auth = ApproverAuthenticator(cred_store, cred_store, {})
    client = ApprovalClient(manager, auth)

    ctx = CLIContext(
        approval_client=client,
        out_stream=out,
        err_stream=err,
        in_stream=in_s,
    )

    shell = RyuInteractiveShell(ctx=ctx, space_id="test-space")
    return shell, ctx, out, err, client, budget


def test_shell_banner_dynamic_n(shell_setup):
    """Banner must dynamically render N from AttentionBudget (Contract APP-003, CLI-009)."""
    shell, ctx, out, _, _, budget = shell_setup

    shell.render_banner()
    output = out.getvalue()
    assert "RYU AI — Cognitive Architecture Developer Shell" in output
    assert "Space: test-space" in output
    assert "Attention: [NORMAL] 0 / 4 active gates" in output

    # Saturate attention
    budget.set_limit("test-space", 2)
    budget._active["test-space"] = {"req-1", "req-2"}
    budget._queues["test-space"][2].append("req-3")

    out.seek(0)
    out.truncate(0)
    shell.render_banner()
    output_sat = out.getvalue()
    assert "Attention: [SATURATED]" in output_sat
    assert "/ 2 active gates" in output_sat


def test_shell_completer_suggestions(shell_setup):
    """Autocompleter must suggest commands and dynamic approval IDs (Contract CLI-010)."""
    shell, _, _, _, client, _ = shell_setup

    from channels.cli.shell import RyuCompleter
    completer = RyuCompleter(shell)

    # Mock document for 'app'
    doc_mock = MagicMock()
    doc_mock.text_before_cursor = "app"
    completions = list(completer.get_completions(doc_mock, None))
    assert any(c.text == "approval" for c in completions)

    # Mock document for 'approval '
    doc_mock.text_before_cursor = "approval "
    completions_sub = list(completer.get_completions(doc_mock, None))
    assert any(c.text == "approve" for c in completions_sub)
    assert any(c.text == "reject" for c in completions_sub)
    assert any(c.text == "inspect" for c in completions_sub)

    # Dynamic completion of pending approval ID
    client.request_approval(
        request_id="app-pending-42",
        space_id="test-space",
        capability="fs.write",
        summary="Test file write",
    )
    doc_mock.text_before_cursor = "approval inspect app-"
    completions_ids = list(completer.get_completions(doc_mock, None))
    assert any(c.text == "app-pending-42" for c in completions_ids)


def test_shell_un_echoed_password_handling(shell_setup):
    """Interactive approval must prompt for un-echoed password when --token is omitted (Contract CLI-011)."""
    shell, ctx, out, _, _, _ = shell_setup

    tokens = ["approval", "approve", "app-pending-42"]

    with patch.object(shell, "prompt_password", return_value="secret-un-echoed-123") as mock_prompt:
        with patch("builtins.input", return_value="admin_user"):
            augmented = shell._handle_interactive_approval_flags(tokens)

    assert augmented is not None
    assert "--approver" in augmented
    assert "admin_user" in augmented
    assert "--token" in augmented
    assert "secret-un-echoed-123" in augmented
    assert "--space-id" in augmented
    assert "test-space" in augmented
    mock_prompt.assert_called_once()


def test_main_shell_subcommand(shell_setup):
    """'ryu shell' must invoke RyuInteractiveShell.run()."""
    _, ctx, _, _, _, _ = shell_setup
    with patch("channels.cli.shell.RyuInteractiveShell.run", return_value=0) as mock_run:
        code = main(["shell"], ctx=ctx)
        assert code == 0
        mock_run.assert_called_once()


def test_shell_conversational_prompt(shell_setup):
    """Conversational prompts like 'write basic python program' must execute via GoalAnalyzer under SCCA §18."""
    shell, ctx, out, err, _, _ = shell_setup

    prompt = "write basic python program"
    shell.handle_prompt(prompt)

    output = out.getvalue()
    assert "Processing goal in space 'test-space'" in output
    assert "single_agent_eligible = True" in output
    assert "Direct Single-Agent Fast Path" in output
    assert "def main()" in output
    assert err.getvalue() == ""


def test_shell_slash_commands_and_repl(shell_setup):
    """Shell REPL must dispatch slash commands and prompts without syntax error."""
    shell, ctx, out, err, _, _ = shell_setup

    # Simulate entering /help and then exit
    with patch("channels.cli.shell.PromptSession.prompt", side_effect=["/help", "write basic python program", "/exit"]):
        shell._session = MagicMock()
        shell._session.prompt.side_effect = ["/help", "write basic python program", "/exit"]
        shell.run()

    output = out.getvalue()
    assert "RYU Developer Shell Commands:" in output
    assert "Conversational Prompts:" in output
    assert "single_agent_eligible = True" in output
    assert "Exiting RYU shell." in output


