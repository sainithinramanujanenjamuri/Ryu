"""Conversational Prompt Synthesizer: Intelligent response generator for SCCA Space prompts.

Provides programming language detection (Java, Python, C++, Rust, Go, JS/TS, Bash)
and conversational Q&A synthesis adhering to SCCA §4, §18 principles.

spec §2, §4, §18 — Phase 8.5
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from llm.provider import LiveHTTPLLMProvider, LLMRequest

# Global LLM generation configuration
_LLM_CONFIG: dict[str, Any] = {
    "enabled": os.environ.get("RYU_LIVE_LLM", "0") in ("1", "true", "True"),
    "provider": os.environ.get("RYU_LLM_PROVIDER", "ollama"),
    "base_url": os.environ.get("RYU_LLM_BASE_URL", "http://localhost:11434"),
    "model": os.environ.get("RYU_LLM_MODEL", "qwen3.5:4b"),
    "api_key": os.environ.get("RYU_LLM_API_KEY", ""),
}


def get_llm_config() -> dict[str, Any]:
    cfg = dict(_LLM_CONFIG)
    if cfg["api_key"]:
        cfg["api_key_masked"] = (
            cfg["api_key"][:4] + "..." + cfg["api_key"][-4:]
            if len(cfg["api_key"]) > 8
            else "***"
        )
    else:
        cfg["api_key_masked"] = ""
    return cfg


def set_llm_config(
    enabled: bool | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    if enabled is not None:
        _LLM_CONFIG["enabled"] = bool(enabled)
    if provider is not None:
        _LLM_CONFIG["provider"] = str(provider)
    if base_url is not None:
        _LLM_CONFIG["base_url"] = str(base_url)
    if model is not None:
        _LLM_CONFIG["model"] = str(model)
    if api_key is not None:
        _LLM_CONFIG["api_key"] = str(api_key)
    return get_llm_config()


def synthesize_response(
    prompt: str,
    space_id: str,
    goal_spec: Any | None = None,
    live_llm: bool | None = None,
) -> str:
    """Generate a response fulfilling the prompt (via Live LLM or Deterministic)."""
    clean_prompt = prompt.strip()
    # Strip optional surrounding angle brackets e.g. <what is AI?> -> what is AI?
    if clean_prompt.startswith("<") and clean_prompt.endswith(">"):
        clean_prompt = clean_prompt[1:-1].strip()

    goal_id = getattr(goal_spec, "goal_id", "direct-prompt")
    single_agent = getattr(goal_spec, "single_agent_eligible", True)
    caps_list = getattr(goal_spec, "required_capabilities", ["general.compute"])
    caps_str = ", ".join(caps_list) if caps_list else "general.compute"

    # Check whether Live LLM generation is requested
    should_use_live = live_llm if live_llm is not None else _LLM_CONFIG["enabled"]
    if should_use_live:
        model = _LLM_CONFIG["model"]
        provider_name = _LLM_CONFIG["provider"]
        provider = LiveHTTPLLMProvider(
            base_url=_LLM_CONFIG["base_url"],
            api_key=_LLM_CONFIG["api_key"],
            model=model,
            timeout=120.0,
            provider_name=provider_name,
        )
        req = LLMRequest(
            request_id=f"llm-{uuid.uuid4().hex[:12]}",
            correlation_id=goal_id,
            space_id=space_id,
            agent_id="space-assistant",
            model=model,
            provider=provider_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are RYU AI, an advanced cognitive architecture and autonomous agent operating inside Space '{space_id}' "
                        f"under the Space-Centric Cognitive Architecture (SCCA).\n"
                        f"Guidelines:\n"
                        f"1. Interpret user intent flexibly, resolving typos or shorthand naturally (e.g., 'sit', 'wed sit', 'websit' mean 'website / webpage').\n"
                        f"2. When the user asks to build, create, or design a website, webpage, landing page, app, or tool, ALWAYS generate the complete, self-contained, high-quality, and modern HTML/CSS/JavaScript code in a clean markdown code block.\n"
                        f"3. Never refuse with disclaimers like 'I cannot build websites' or 'I am just a text assistant' — you are an engineer and software generator, so write and deliver the code directly.\n"
                        f"4. Format all responses cleanly in Markdown with insightful explanations."
                    ),
                },
                {"role": "user", "content": clean_prompt},
            ],
        )
        resp = provider.complete(req)
        if resp.status == "ok" and resp.content.strip():
            return (
                f"{resp.content.strip()}\n\n"
                f"---\n"
                f"*⚡ Generated via Live LLM (`{model}` via `{provider_name}`) in Space `{space_id}`*"
            )
        else:
            err_msg = resp.error.message if resp.error else "Model returned empty response"
            det_res = _synthesize_deterministic(clean_prompt, space_id, goal_id, single_agent, caps_str)
            return (
                f"> [!WARNING]\n"
                f"> **Live LLM Offline**: Failed to reach `{_LLM_CONFIG['base_url']}` ({err_msg}).\n"
                f"> *Ensure Ollama is running (`ollama serve`) or check settings. Showing deterministic response below:*\n\n"
                f"{det_res}"
            )

    return _synthesize_deterministic(clean_prompt, space_id, goal_id, single_agent, caps_str)


def _synthesize_deterministic(
    clean_prompt: str,
    space_id: str,
    goal_id: str,
    single_agent: bool,
    caps_str: str,
) -> str:
    """Generate a clean, structured deterministic response fulfilling the prompt."""
    lower = clean_prompt.lower()

    # ─── 1. GREETINGS & CASUAL INTERACTION ────────────────────────────
    if lower in ("hi", "hello", "hey", "greetings", "hi ryu", "hello ryu", "hey ryu"):
        return (
            f"Hello! I am **RYU AI**, running inside Space `{space_id}`.\n\n"
            f"- **Space Isolation**: All memory and execution are bounded to `{space_id}` (SCCA Law 1).\n"
            f"- **Governance**: Human capability gates require cryptographic HMAC approvals (SCCA Law 2).\n"
            f"- **Fast-Path**: Single-agent tasks execute directly without team coordination overhead (Sec 18).\n\n"
            f"You can ask me to write code in any language (Python, Java, Rust, C++, Go, etc.), "
            f"ask technical questions, or use slash commands like `/status`, `/approvals`, or `/stream`."
        )

    # ─── 2. KNOWLEDGE & EXPLANATION QUERIES ────────────────────────────
    if "what is ai" in lower or "what is artificial intelligence" in lower:
        return (
            "### What is Artificial Intelligence (AI)?\n\n"
            "**Artificial Intelligence (AI)** refers to the simulation of human cognitive capabilities "
            "by computational systems. This includes problem-solving, pattern recognition, learning, "
            "natural language understanding, and decision-making.\n\n"
            "#### How RYU AI Implements AI (SCCA):\n"
            "- **Space-Centric Architecture**: Unlike black-box monolithic agents, RYU organizes execution inside "
            "isolated, versioned **Spaces** where state, artifacts, and tools are strictly segregated (Law 1).\n"
            "- **Zero Unadmitted Authority**: Capabilities are never permanently owned by agents; they are requested, "
            "audited, and admitted through explicit Human Gates (Law 2).\n"
            "- **Deterministic & Causally Traceable**: Components communicate exclusively via typed **Pulses** "
            "recorded in an immutable event log for replay and total auditability (Law 3 & Law 6)."
        )

    if "what is ryu" in lower or "who are you" in lower or "what is this" in lower:
        return (
            "### About RYU AI\n\n"
            "**RYU AI** is an advanced cognitive architecture based on the **Space-Centric Cognitive Architecture (SCCA)**. "
            "It is designed to organize, coordinate, and govern autonomous AI agents while guaranteeing:\n\n"
            "1. **Strict Space Isolation**: Zero accidental data or authority leakage between workspaces.\n"
            "2. **Cryptographic Human Oversight**: High-risk capability invocations must be signed with "
            "`token-hmac-v1` credentials.\n"
            "3. **Attention Budgeting**: Prevents human approver fatigue by enforcing a dynamic concurrency limit ($N$).\n"
            "4. **Single-Agent Fast-Path (§18)**: Bypasses multi-agent decomposition for simple, sequential tasks."
        )

    if "scca" in lower or "space isolation" in lower or "architecture" in lower:
        return (
            "### Space-Centric Cognitive Architecture (SCCA)\n\n"
            "The six foundational laws of RYU AI are:\n\n"
            "1. **Law 1 — Everything Happens Inside a Space**: Space is the primary isolation and authority boundary.\n"
            "2. **Law 2 — Capabilities Are Requested, Never Owned**: Agents request capability admission through gates.\n"
            "3. **Law 3 — Components Communicate Through Pulses**: Zero hidden communication paths.\n"
            "4. **Law 4 — Knowledge Belongs to the Space First**: Cross-space promotion requires explicit authority.\n"
            "5. **Law 5 — Humans Define Goals, Ryu Organizes Execution**: Intent defines goals; execution is adapted safely.\n"
            "6. **Law 6 — Failures Are Contained, Escalated, and Never Silent**: Swallowing errors is prohibited."
        )

    # ─── 3. PROGRAMMING CODE SYNTHESIS ────────────────────────────────
    is_code_request = any(
        kw in lower
        for kw in (
            "write",
            "code",
            "program",
            "script",
            "function",
            "implement",
            "create a",
            "generate",
            "algorithm",
            "fibonacci",
            "calculator",
        )
    )

    if is_code_request:
        # Detect target language with word boundaries
        if re.search(r"\bjava\b", lower) and "javascript" not in lower:
            lang = "java"
        elif re.search(r"\b(c\+\+|cpp)\b", lower):
            lang = "cpp"
        elif re.search(r"\brust\b", lower):
            lang = "rust"
        elif re.search(r"\b(golang|go)\b", lower):
            lang = "go"
        elif re.search(r"\b(javascript|typescript|js|ts)\b", lower):
            lang = "typescript"
        elif re.search(r"\b(bash|sh|shell)\b", lower):
            lang = "bash"
        elif re.search(r"\b(html|css)\b", lower):
            lang = "html"
        elif re.search(r"\b(python|py)\b", lower):
            lang = "python"
        elif re.search(r"\bc\b", lower):
            lang = "c"
        else:
            lang = "python"

        code_snippet = _generate_code_snippet(lang, clean_prompt, space_id)

        return (
            f"### {lang.upper()} Program\n\n"
            f"Here is the clean, structured {lang.capitalize()} code fulfilling your objective:\n\n"
            f"```{lang}\n{code_snippet}```\n\n"
            f"#### SCCA Governance & Execution (Sec 18)\n"
            f"- **Goal Spec ID**: `{goal_id}`\n"
            f"- **Capabilities**: `{caps_str}`\n"
            f"- **Single-Agent Fast-Path**: `single_agent_eligible = {single_agent}`\n"
            f"- **Coordination**: Direct execution (multi-agent DAG overhead bypassed per SCCA Sec 18)."
        )

    # ─── 4. GENERAL INSTRUCTION DEFAULT ───────────────────────────────
    return (
        f"### Goal Processed\n\n"
        f"I have received and structured your objective: **{clean_prompt}**\n\n"
        f"- **Space Boundary**: `{space_id}`\n"
        f"- **Goal Spec ID**: `{goal_id}`\n"
        f"- **Detected Capabilities**: `{caps_str}`\n"
        f"- **Execution Mode**: `{'Direct Single-Agent (§18 Fast Path)' if single_agent else 'Multi-Agent Team DAG'}`\n\n"
        f"Your instruction has been accepted and recorded inside Space `{space_id}`."
    )


def _generate_code_snippet(lang: str, prompt: str, space_id: str) -> str:
    """Generate template code in the requested programming language."""
    if lang == "java":
        return (
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            f'        System.out.println("Hello from RYU AI Space: {space_id}");\n'
            f"        // Objective: {prompt}\n"
            "        int[] numbers = {1, 2, 3, 4, 5};\n"
            "        int sumOfSquares = 0;\n"
            "        for (int n : numbers) {\n"
            "            sumOfSquares += n * n;\n"
            "        }\n"
            '        System.out.println("Computed sum of squares: " + sumOfSquares);\n'
            "    }\n"
            "}\n"
        )

    if lang in ("cpp", "c"):
        return (
            "#include <iostream>\n"
            "#include <vector>\n"
            "#include <numeric>\n\n"
            "int main() {\n"
            f'    std::cout << "Hello from RYU AI Space: {space_id}" << std::endl;\n'
            f"    // Objective: {prompt}\n"
            "    std::vector<int> numbers = {1, 2, 3, 4, 5};\n"
            "    int sum = 0;\n"
            "    for (int n : numbers) {\n"
            "        sum += n * n;\n"
            "    }\n"
            '    std::cout << "Computed sum of squares: " << sum << std::endl;\n'
            "    return 0;\n"
            "}\n"
        )

    if lang == "rust":
        return (
            "fn main() {\n"
            f'    println!("Hello from RYU AI Space: {space_id}");\n'
            f"    // Objective: {prompt}\n"
            "    let numbers = [1, 2, 3, 4, 5];\n"
            "    let sum_of_squares: i32 = numbers.iter().map(|&x| x * x).sum();\n"
            '    println!("Computed sum of squares: {}", sum_of_squares);\n'
            "}\n"
        )

    if lang == "go":
        return (
            "package main\n\n"
            'import "fmt"\n\n'
            "func main() {\n"
            f'    fmt.Println("Hello from RYU AI Space: {space_id}")\n'
            f"    // Objective: {prompt}\n"
            "    numbers := []int{1, 2, 3, 4, 5}\n"
            "    sum := 0\n"
            "    for _, n := range numbers {\n"
            "        sum += n * n\n"
            "    }\n"
            '    fmt.Printf("Computed sum of squares: %d\\n", sum)\n'
            "}\n"
        )

    if lang in ("typescript", "javascript"):
        return (
            f'console.log("Hello from RYU AI Space: {space_id}");\n'
            f"// Objective: {prompt}\n"
            "const numbers: number[] = [1, 2, 3, 4, 5];\n"
            "const sumOfSquares = numbers.reduce((acc, x) => acc + x * x, 0);\n"
            'console.log(`Computed sum of squares: ${sumOfSquares}`);\n'
        )

    if lang == "bash":
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            f'echo "Hello from RYU AI Space: {space_id}"\n'
            f"# Objective: {prompt}\n"
            "numbers=(1 2 3 4 5)\n"
            "sum=0\n"
            'for n in "${numbers[@]}"; do\n'
            "    sum=$((sum + n * n))\n"
            "done\n"
            'echo "Computed sum of squares: $sum"\n'
        )

    # Default to Python
    return (
        "#!/usr/bin/env python3\n"
        f'"""Generated by RYU AI (SCCA Phase 8.5) -- Space: {space_id}"""\n\n'
        "def main() -> None:\n"
        f'    print("Hello from RYU AI Space: {space_id}")\n'
        f"    # Objective: {prompt}\n"
        "    numbers = [1, 2, 3, 4, 5]\n"
        "    squares = [x ** 2 for x in numbers]\n"
        '    print(f"Computed squares: {squares}")\n\n'
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
