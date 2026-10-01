"""Deterministic Test Result Parser (ADR-0044, EVIDENCE-001, EVIDENCE-002).

Extracts structured test execution metrics from raw runner output.
Enforces the invariant that exit code 0 alone is insufficient without
structured parser confirmation. Detects ambiguous or contradictory output
and marks evidence as INCONCLUSIVE.
"""

from __future__ import annotations

import re

from core.space.test_execution_protocol import (
    TestFailure,
)

# Pytest summary line regexes
# e.g.: "=== 5 passed, 1 skipped, 2 failed in 0.42s ==="
# e.g.: "=== 1 failed in 0.12s ==="
# e.g.: "=== 10 passed in 1.23s ==="
_PYTEST_SUMMARY_REGEX = re.compile(
    r"=+\s*(?P<counts>.*?)(?:\s+in\s+(?P<duration>[\d\.]+)\s*s)?\s*=+",
    re.IGNORECASE,
)

_PYTEST_PASSED_REGEX = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_PYTEST_FAILED_REGEX = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_PYTEST_SKIPPED_REGEX = re.compile(r"(\d+)\s+skipped", re.IGNORECASE)
_PYTEST_ERRORS_REGEX = re.compile(r"(\d+)\s+error", re.IGNORECASE)
_PYTEST_XFAILED_REGEX = re.compile(r"(\d+)\s+xfailed", re.IGNORECASE)
_PYTEST_XPASSED_REGEX = re.compile(r"(\d+)\s+xpassed", re.IGNORECASE)

# Pytest individual failure line regex
# e.g.: "FAILED tests/test_app.py::test_calculation - AssertionError: 4 != 5"
_PYTEST_FAILED_LINE_REGEX = re.compile(
    r"^FAILED\s+(?P<node>[^\s]+)(?:\s+-\s+(?P<msg>.*))?",
    re.IGNORECASE,
)
_PYTEST_ERROR_LINE_REGEX = re.compile(
    r"^ERROR\s+(?P<node>[^\s]+)(?:\s+-\s+(?P<msg>.*))?",
    re.IGNORECASE,
)

# Unittest regexes
# e.g.: "Ran 12 tests in 0.045s"
_UNITTEST_RAN_REGEX = re.compile(
    r"Ran\s+(?P<count>\d+)\s+tests?\s+in\s+(?P<duration>[\d\.]+)s",
    re.IGNORECASE,
)
_UNITTEST_FAILED_REGEX = re.compile(
    r"FAILED\s+\((?:failures=(?P<failures>\d+))?(?:,\s*)?(?:errors=(?P<errors>\d+))?(?:,\s*)?(?:skipped=(?P<skipped>\d+))?\)",
    re.IGNORECASE,
)


class TestOutputParser:
    """Deterministic parser converting raw runner output into structured test metrics."""

    @classmethod
    def parse(
        cls,
        runner: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        elapsed_duration: float = 0.0,
    ) -> tuple[int, int, int, int, int, float, tuple[TestFailure, ...], str]:
        """Parse runner stdout and stderr.

        Returns:
            tuple of (total_tests, passed, failed, skipped, errors, duration, failures, parser_status)
        """
        combined = f"{stdout}\n{stderr}"

        if "unittest" in runner:
            return cls._parse_unittest(exit_code, combined, elapsed_duration)
        else:
            return cls._parse_pytest(exit_code, combined, elapsed_duration)

    @classmethod
    def _parse_pytest(
        cls,
        exit_code: int,
        output: str,
        elapsed_duration: float,
    ) -> tuple[int, int, int, int, int, float, tuple[TestFailure, ...], str]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]

        passed = 0
        failed = 0
        skipped = 0
        errors = 0
        duration = elapsed_duration
        found_summary = False

        # Scan for summary line backwards (it appears near the end of output)
        for line in reversed(lines):
            match = _PYTEST_SUMMARY_REGEX.search(line)
            if match:
                counts_str = match.group("counts")
                dur_str = match.group("duration")
                if dur_str:
                    try:
                        duration = float(dur_str)
                    except ValueError:
                        pass

                # Extract individual counts from the counts chunk
                p_match = _PYTEST_PASSED_REGEX.search(counts_str)
                f_match = _PYTEST_FAILED_REGEX.search(counts_str)
                s_match = _PYTEST_SKIPPED_REGEX.search(counts_str)
                e_match = _PYTEST_ERRORS_REGEX.search(counts_str)

                if p_match:
                    passed = int(p_match.group(1))
                if f_match:
                    failed = int(f_match.group(1))
                if s_match:
                    skipped = int(s_match.group(1))
                if e_match:
                    errors = int(e_match.group(1))

                if "no tests ran" in counts_str.lower():
                    found_summary = True
                    break

                if p_match or f_match or s_match or e_match:
                    found_summary = True
                    break

        total_tests = passed + failed + skipped + errors

        # Extract structured test failures
        failures_list: list[TestFailure] = []
        seen_nodes: set[str] = set()
        for line in lines:
            f_match = _PYTEST_FAILED_LINE_REGEX.search(line)
            if f_match:
                node = f_match.group("node")
                if node not in seen_nodes:
                    seen_nodes.add(node)
                    raw_msg = (f_match.group("msg") or "").strip()
                    fail_type = "AssertionError"
                    if ":" in raw_msg:
                        fail_type = raw_msg.split(":")[0].strip() or "AssertionError"
                    failures_list.append(
                        TestFailure(
                            test_node_id=node,
                            failure_type=fail_type,
                            message=raw_msg or "Test failed",
                            file_path=node.split("::")[0] if "::" in node else None,
                        )
                    )

            e_match = _PYTEST_ERROR_LINE_REGEX.search(line)
            if e_match:
                node = e_match.group("node")
                if node not in seen_nodes:
                    seen_nodes.add(node)
                    raw_msg = (e_match.group("msg") or "").strip()
                    fail_type = "Error"
                    if ":" in raw_msg:
                        fail_type = raw_msg.split(":")[0].strip() or "Error"
                    failures_list.append(
                        TestFailure(
                            test_node_id=node,
                            failure_type=fail_type,
                            message=raw_msg or "Test collection or fixture error",
                            file_path=node.split("::")[0] if "::" in node else None,
                        )
                    )

        failures = tuple(failures_list)

        # Integrity checks & INCONCLUSIVE determination (EVIDENCE-001, EVIDENCE-002)
        if not found_summary:
            # Could not parse any summary line
            return (
                total_tests,
                passed,
                failed,
                skipped,
                errors,
                duration,
                failures,
                "inconclusive",
            )

        # If exit_code == 0 but we parsed failed/errors > 0 -> contradiction
        if exit_code == 0 and (failed > 0 or errors > 0):
            return (
                total_tests,
                passed,
                failed,
                skipped,
                errors,
                duration,
                failures,
                "inconclusive",
            )

        # If exit_code != 0 but 0 failures and 0 errors found -> contradiction / unparsed error
        if exit_code != 0 and failed == 0 and errors == 0:
            return (
                total_tests,
                passed,
                failed,
                skipped,
                errors,
                duration,
                failures,
                "inconclusive",
            )

        return (
            total_tests,
            passed,
            failed,
            skipped,
            errors,
            duration,
            failures,
            "ok",
        )

    @classmethod
    def _parse_unittest(
        cls,
        exit_code: int,
        output: str,
        elapsed_duration: float,
    ) -> tuple[int, int, int, int, int, float, tuple[TestFailure, ...], str]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]

        total_tests = 0
        passed = 0
        failed = 0
        skipped = 0
        errors = 0
        duration = elapsed_duration
        found_ran = False

        for line in lines:
            ran_match = _UNITTEST_RAN_REGEX.search(line)
            if ran_match:
                total_tests = int(ran_match.group("count"))
                duration = float(ran_match.group("duration"))
                found_ran = True

            fail_match = _UNITTEST_FAILED_REGEX.search(line)
            if fail_match:
                if fail_match.group("failures"):
                    failed = int(fail_match.group("failures"))
                if fail_match.group("errors"):
                    errors = int(fail_match.group("errors"))
                if fail_match.group("skipped"):
                    skipped = int(fail_match.group("skipped"))

        if not found_ran:
            return 0, 0, 0, 0, 0, duration, (), "inconclusive"

        if exit_code == 0 and failed == 0 and errors == 0:
            passed = total_tests - skipped
            return total_tests, passed, failed, skipped, errors, duration, (), "ok"

        passed = max(0, total_tests - failed - errors - skipped)
        status = "ok" if (failed > 0 or errors > 0) else "inconclusive"
        return total_tests, passed, failed, skipped, errors, duration, (), status
