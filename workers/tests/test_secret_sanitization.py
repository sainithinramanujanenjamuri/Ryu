"""Unit tests for secret sanitization and containment in worker output and logs.

spec §10, CONTRACT_MATRIX SECRET-004, SECRET-005, ADR-0011, ADR-0013
"""

from workers.base import sanitize_text
from workers.contract import ExecutionRequest, WorkerIdentity
from workers.python.worker import PythonWorker


def test_sanitize_text_redacts_tokens_and_keys() -> None:
    raw = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and token: my_secret_token_12345"
    sanitized = sanitize_text(raw)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in sanitized
    assert "my_secret_token_12345" not in sanitized
    assert "[REDACTED]" in sanitized


def test_sanitize_text_preserves_opaque_secret_uris() -> None:
    raw = "Resolved reference to secret://credentials/api_key"
    sanitized = sanitize_text(raw)
    assert "secret://credentials/api_key" in sanitized


def test_worker_redacts_secrets_in_output_and_logs() -> None:
    ident = WorkerIdentity(
        worker_id="sec-worker-01",
        capability="python.eval_sandboxed",
        space_id="space-1",
    )
    worker = PythonWorker(identity=ident)

    code = (
        "print('password: super_secret_password_123'); "
        "import sys; sys.stderr.write('token: my_secret_token_abc')"
    )
    req = ExecutionRequest(
        request_id="req-sec-1",
        correlation_id="corr-sec-1",
        space_id="space-1",
        worker_id="sec-worker-01",
        capability="python.eval_sandboxed",
        arguments={"code": code},
    )

    res = worker.execute(req)
    assert "super_secret_password_123" not in str(res.output_data)
    assert "super_secret_password_123" not in "".join(res.logs)
