"""RYU AI Interactive Developer CLI Shell.

Provides a rich interactive terminal shell session with persistent history,
contextual autocompletion, dynamic attention budget visualization, and
un-echoed password entry for human approver secrets.

spec §2, §4, ADR-0026, CONTRACT CLI-009, CLI-010, CLI-011 — Phase 8.5
"""

from __future__ import annotations

import getpass
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from channels.cli.context import CLIContext

# Try importing prompt_toolkit components
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


COMMAND_TREE: dict[str, list[str]] = {
    "status": [],
    "space": ["list", "inspect", "use"],
    "approval": ["list", "inspect", "approve", "reject"],
    "task": ["list", "inspect"],
    "audit": ["stream"],
    "clear": [],
    "help": [],
    "exit": [],
    "quit": [],
    "prompt": [],
    "/status": [],
    "/space": ["list", "inspect", "use"],
    "/spaces": [],
    "/approval": ["list", "inspect", "approve", "reject"],
    "/approvals": [],
    "/task": ["list", "inspect"],
    "/tasks": [],
    "/audit": ["stream"],
    "/stream": [],
    "/prompt": [],
    "/clear": [],
    "/help": [],
    "/exit": [],
    "/quit": [],
}


if PROMPT_TOOLKIT_AVAILABLE:
    class RyuCompleter(Completer):
        """Autocompletion for RYU interactive commands, subcommands, and active approval IDs."""

        def __init__(self, shell: RyuInteractiveShell) -> None:
            self.shell = shell

        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor.lstrip()
            words = text.split()

            if not words or (len(words) == 1 and not document.text_before_cursor.endswith(" ")):
                prefix = words[0] if words else ""
                for cmd in COMMAND_TREE:
                    if cmd.startswith(prefix):
                        yield Completion(cmd, start_position=-len(prefix))
                return

            cmd = words[0]
            if cmd in COMMAND_TREE:
                subcmds = COMMAND_TREE[cmd]
                if len(words) == 1 and document.text_before_cursor.endswith(" "):
                    for sub in subcmds:
                        yield Completion(sub, start_position=0)
                    return

                if len(words) == 2 and not document.text_before_cursor.endswith(" "):
                    sub_prefix = words[1]
                    for sub in subcmds:
                        if sub.startswith(sub_prefix):
                            yield Completion(sub, start_position=-len(sub_prefix))
                    return

                # Autocomplete approval IDs for inspect/approve/reject
                if cmd == "approval" and len(words) >= 2 and words[1] in ("inspect", "approve", "reject"):
                    curr_prefix = words[2] if len(words) >= 3 and not document.text_before_cursor.endswith(" ") else ""
                    app_ids = self._get_pending_approval_ids()
                    for aid in app_ids:
                        if aid.startswith(curr_prefix):
                            yield Completion(aid, start_position=-len(curr_prefix))

        def _get_pending_approval_ids(self) -> list[str]:
            ctx = self.shell.ctx
            if ctx.approval_client is not None and hasattr(ctx.approval_client, "list_pending"):
                try:
                    reqs = ctx.approval_client.list_pending(self.shell.current_space_id)
                    return [r.request_id for r in reqs]
                except Exception:
                    pass
            return []


class RyuInteractiveShell:
    """Interactive CLI shell session for RYU AI."""

    def __init__(self, ctx: CLIContext, space_id: str = "default") -> None:
        self.ctx = ctx
        self.current_space_id = space_id
        self.history_file = Path.home() / ".ryu" / "cli_history"
        self._session: Any = None
        self._init_session()

    def _init_session(self) -> None:
        if not PROMPT_TOOLKIT_AVAILABLE:
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(self.history_file))
        except Exception:
            history = None

        style = Style.from_dict({
            "prompt": "#d4af37 bold",
            "space": "#3a506b italic",
        })

        try:
            self._session = PromptSession(
                history=history,
                completer=RyuCompleter(self),
                style=style,
            )
        except Exception:
            self._session = None

    def get_dynamic_attention_info(self) -> tuple[int, int, int, bool]:
        """
        Return (concurrency_limit, active_count, queued_count, is_saturated).
        Dynamically reads from runtime AttentionBudget. Never hardcodes N.
        """
        limit = 3
        active_count = 0
        queued_count = 0
        is_saturated = False

        client = self.ctx.approval_client
        if client is not None and hasattr(client, "manager"):
            mgr = client.manager
            if hasattr(mgr, "attention_budget") and mgr.attention_budget is not None:
                budget = mgr.attention_budget
                limit = budget.get_limit(self.current_space_id)
                is_saturated = budget.is_saturated(self.current_space_id)

            reqs = mgr.store.list_by_space(self.current_space_id)
            for r in reqs:
                if r.queue_state == "active" and r.status == "pending":
                    active_count += 1
                elif r.queue_state == "queued":
                    queued_count += 1

            if not is_saturated:
                is_saturated = active_count >= limit

        return limit, active_count, queued_count, is_saturated

    def render_banner(self) -> None:
        """Render the developer command center banner with dynamic N attention status."""
        limit, active_count, queued_count, is_saturated = self.get_dynamic_attention_info()

        if is_saturated:
            att_status = f"[SATURATED] {active_count} / {limit} active gates ({queued_count} queued)"
        else:
            att_status = f"[NORMAL] {active_count} / {limit} active gates"

        banner = [
            "┌────────────────────────────────────────────────────────────────────────┐",
            "│  RYU AI — Cognitive Architecture Developer Shell (SCCA Phase 8.5)       │",
            f"│  Space: {self.current_space_id:<12} | Status: ONLINE | Taint: UNTAINTED (TTY)          │",
            f"│  Attention: {att_status:<58} │",
            "│  Type 'help' for commands, 'exit' or Ctrl+D to quit.                   │",
            "└────────────────────────────────────────────────────────────────────────┘",
        ]
        self.ctx.write_out("\n".join(banner))

    def prompt_password(self, prompt_text: str = "Approver Secret Key: ") -> str:
        """Prompt for secret key with un-echoed password entry (Contract CLI-011)."""
        if PROMPT_TOOLKIT_AVAILABLE and self._session is not None:
            return self._session.prompt(prompt_text, is_password=True).strip()
        else:
            return getpass.getpass(prompt_text).strip()

    def run(self) -> int:
        """Main interactive read-eval-print loop."""
        self.render_banner()

        while True:
            prompt_str = f"ryu [{self.current_space_id}]> "
            try:
                if PROMPT_TOOLKIT_AVAILABLE and self._session is not None:
                    line = self._session.prompt(prompt_str)
                else:
                    line = input(prompt_str)
            except (EOFError, KeyboardInterrupt):
                self.ctx.write_out("\nExiting RYU shell.")
                break

            line = line.strip()
            if not line:
                continue

            # Check built-in shell commands
            if line.lower() in ("exit", "quit", "q"):
                self.ctx.write_out("Exiting RYU shell.")
                break

            if line.lower() == "clear":
                if os.name == "nt":
                    os.system("cls")
                else:
                    os.system("clear")
                self.render_banner()
                continue

            if line.lower() == "help":
                self._render_help()
                continue

            try:
                tokens = shlex.split(line)
            except ValueError as e:
                self.ctx.write_err(f"Parse error: {e}")
                continue

            if not tokens:
                continue

            # Normalize slash commands
            if tokens[0].startswith("/"):
                raw_slash = tokens[0].lower()
                rest = tokens[1:]
                if raw_slash in ("/exit", "/quit", "/q"):
                    self.ctx.write_out("Exiting RYU shell.")
                    break
                elif raw_slash == "/clear":
                    if os.name == "nt":
                        os.system("cls")
                    else:
                        os.system("clear")
                    self.render_banner()
                    continue
                elif raw_slash == "/help":
                    self._render_help()
                    continue
                elif raw_slash == "/status":
                    tokens = ["status"] + rest
                elif raw_slash in ("/space", "/spaces"):
                    if not rest:
                        tokens = ["space", "list"]
                    elif rest[0] == "use":
                        if len(rest) < 2:
                            self.ctx.write_err("Usage: /space use <space_id>")
                            continue
                        self.current_space_id = rest[1]
                        self.ctx.write_out(f"Switched active space context to '{self.current_space_id}'.")
                        continue
                    else:
                        tokens = ["space"] + rest
                elif raw_slash in ("/approval", "/approvals"):
                    if not rest:
                        tokens = ["approval", "list"]
                    else:
                        tokens = ["approval"] + rest
                elif raw_slash in ("/task", "/tasks"):
                    if not rest:
                        tokens = ["task", "list"]
                    else:
                        tokens = ["task"] + rest
                elif raw_slash in ("/stream", "/audit"):
                    if raw_slash == "/stream":
                        tokens = ["audit", "stream"] + rest
                    else:
                        if not rest:
                            tokens = ["audit", "stream"]
                        else:
                            tokens = ["audit"] + rest
                elif raw_slash == "/prompt":
                    if rest:
                        self.handle_prompt(" ".join(rest))
                    else:
                        self.ctx.write_err("Usage: /prompt <objective>")
                    continue
                else:
                    self.ctx.write_err(f"Unknown slash command: {tokens[0]}. Type /help for available commands.")
                    continue

            # Handle space use <space_id>
            if len(tokens) >= 2 and tokens[0] == "space" and tokens[1] == "use":
                if len(tokens) < 3:
                    self.ctx.write_err("Usage: space use <space_id>")
                else:
                    self.current_space_id = tokens[2]
                    self.ctx.write_out(f"Switched active space context to '{self.current_space_id}'.")
                continue

            # Check if this is an explicit prompt command
            if tokens[0] == "prompt":
                if len(tokens) > 1:
                    self.handle_prompt(" ".join(tokens[1:]))
                else:
                    self.ctx.write_err("Usage: prompt <objective>")
                continue

            # Check if this is an administrative subcommand
            known_subcommands = {"status", "space", "approval", "task", "audit", "shell", "prompt"}
            if tokens[0] not in known_subcommands:
                # Natural language conversational prompt / goal (SCCA §4, §18)
                self.handle_prompt(line)
                continue

            # Intercept approval approve / reject to handle un-echoed secret prompting (Contract CLI-011)
            if len(tokens) >= 2 and tokens[0] == "approval" and tokens[1] in ("approve", "reject"):
                tokens = self._handle_interactive_approval_flags(tokens)
                if tokens is None:
                    continue

            # Delegate to standard CLI main router
            from channels.cli.main import main as cli_main
            try:
                cli_main(tokens, ctx=self.ctx)
            except Exception as e:
                self.ctx.write_err(f"Command execution error: {e}")

        return 0

    def _handle_interactive_approval_flags(self, tokens: list[str]) -> list[str] | None:
        """
        Intercept interactive approval approve/reject invocations.
        If --approver or --token are missing, prompt securely.
        """
        subcmd = tokens[1]  # 'approve' or 'reject'
        if len(tokens) < 3:
            self.ctx.write_err(f"Usage: approval {subcmd} <approval_id> [--approver ID] [--token KEY]")
            return None

        approval_id = tokens[2]
        # Check if approver flag is present
        has_approver = "--approver" in tokens or "-a" in tokens
        has_token = "--token" in tokens or "-t" in tokens

        approver_id = ""
        if not has_approver:
            # Resolve default or prompt
            default_approver = "human_operator"
            if self.ctx.approval_client is not None and hasattr(self.ctx.approval_client, "manager"):
                default_approver = self.ctx.approval_client.manager.get_approver_id(self.current_space_id)
            try:
                prompt_input = input(f"Approver ID [{default_approver}]: ").strip()
                approver_id = prompt_input if prompt_input else default_approver
            except (EOFError, KeyboardInterrupt):
                self.ctx.write_out("\nOperation cancelled.")
                return None
            tokens.extend(["--approver", approver_id])

        if not has_token:
            try:
                token_secret = self.prompt_password("Approver Secret Key: ")
                if not token_secret:
                    self.ctx.write_err("Error: Approver secret key cannot be empty.")
                    return None
            except (EOFError, KeyboardInterrupt):
                self.ctx.write_out("\nOperation cancelled.")
                return None
            tokens.extend(["--token", token_secret])

        # Automatically pass current space_id if not specified
        if "--space-id" not in tokens and "-s" not in tokens:
            tokens.extend(["--space-id", self.current_space_id])

        return tokens

    def handle_prompt(self, prompt: str) -> None:
        """Handle conversational natural language prompt within active Space context (SCCA §4, §18)."""
        import time
        from core.orchestrator.goal_analyzer import Command, GoalAnalyzer

        self.ctx.write_out(f"\nProcessing goal in space '{self.current_space_id}'...")

        # 1. Goal Analysis
        cmd_id = f"cmd-{int(time.time() * 1000)}"
        bus = None
        if self.ctx.approval_client is not None and hasattr(self.ctx.approval_client, "manager"):
            bus = getattr(self.ctx.approval_client.manager, "bus", None)

        analyzer = GoalAnalyzer(bus=bus)
        command = Command(
            command_id=cmd_id,
            space_id=self.current_space_id,
            objective=prompt,
        )

        try:
            goal_spec = analyzer.analyze_goal(command)
        except Exception as e:
            self.ctx.write_err(f"Goal analysis failed: {e}")
            return

        caps_str = ", ".join(goal_spec.required_capabilities) if goal_spec.required_capabilities else "general.compute"
        single_agent = goal_spec.single_agent_eligible

        # 2. Render SCCA Goal Specification Card
        card = [
            f"+-- Goal Spec: {goal_spec.goal_id} " + "-" * 35,
            f"| Objective:    {goal_spec.objective}",
            f"| Capabilities: {caps_str}",
            f"| SCCA Sec 18:  single_agent_eligible = {single_agent}",
            f"| Execution:    {'Direct Single-Agent Fast Path (Multi-Agent DAG Bypassed)' if single_agent else 'Multi-Agent Team DAG Required'}",
            "+" + "-" * 55,
        ]
        self.ctx.write_out("\n".join(card) + "\n")

        # 3. Synthesize intelligent multi-language code or Q&A response
        from channels.synthesizer import synthesize_response
        response_text = synthesize_response(prompt, self.current_space_id, goal_spec)
        self.ctx.write_out(response_text + "\n")

    def _render_help(self) -> None:
        help_text = [
            "RYU Developer Shell Commands:",
            "",
            "  Conversational Prompts:",
            "    <any text>                 Submit natural language goal to current space",
            "                               Example: 'write basic python program'",
            "",
            "  Slash Commands:",
            "    /status                    Show runtime status and dynamic attention budget",
            "    /space [list|use <id>]     List or switch active space",
            "    /approvals                 List pending capability approvals",
            "    /approval approve <id>     Approve a capability gate (secure token prompt)",
            "    /approval reject <id>      Reject a capability gate (secure token prompt)",
            "    /tasks                     List execution plan tasks",
            "    /stream [--limit N]        Stream immutable pulses",
            "    /clear                     Clear terminal screen and redraw status banner",
            "    /help                      Show this help menu",
            "    /exit, /quit               Exit shell session",
            "",
            "  Standard Subcommands:",
            "    status, space, approval, task, audit, clear, help, exit",
        ]
        self.ctx.write_out("\n".join(help_text))

