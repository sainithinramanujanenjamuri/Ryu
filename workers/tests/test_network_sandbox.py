"""Unit tests for Network Sandbox and egress policy enforcement.

spec §7, §10, CONTRACT_MATRIX WORKER-003, ADR-0014
"""

import socket

import pytest

from workers.contract import NetworkPolicy, NetworkPolicyMode
from workers.sandbox.network import NetworkSandbox


def test_network_disabled_mode_blocks_all() -> None:
    policy = NetworkPolicy(mode=NetworkPolicyMode.DISABLED)
    sandbox = NetworkSandbox(policy)

    assert not policy.is_allowed("example.com", 80)
    assert not policy.is_allowed("127.0.0.1", 8080)

    with pytest.raises(PermissionError, match="Network egress denied by policy"):
        sandbox.validate_connection("example.com", 80)


def test_network_restricted_mode_enforcement() -> None:
    policy = NetworkPolicy(
        mode=NetworkPolicyMode.RESTRICTED,
        allowed_hosts=["api.allowed.com"],
        allowed_ports=[443],
        allow_loopback=False,
    )
    sandbox = NetworkSandbox(policy)

    # Allowed host and port
    assert policy.is_allowed("api.allowed.com", 443)
    sandbox.validate_connection("api.allowed.com", 443)

    # Denied host
    assert not policy.is_allowed("evil.com", 443)
    with pytest.raises(PermissionError):
        sandbox.validate_connection("evil.com", 443)

    # Denied port
    assert not policy.is_allowed("api.allowed.com", 80)
    with pytest.raises(PermissionError):
        sandbox.validate_connection("api.allowed.com", 80)

    # Denied loopback
    assert not policy.is_allowed("127.0.0.1", 443)
    with pytest.raises(PermissionError):
        sandbox.validate_connection("127.0.0.1", 443)


def test_network_allowed_mode() -> None:
    policy = NetworkPolicy(mode=NetworkPolicyMode.ALLOWED)
    sandbox = NetworkSandbox(policy)

    assert policy.is_allowed("any-domain.org", 8080)
    sandbox.validate_connection("any-domain.org", 8080)


def test_socket_connect_interception() -> None:
    policy = NetworkPolicy(mode=NetworkPolicyMode.DISABLED)
    sandbox = NetworkSandbox(policy)

    with sandbox.intercept_sockets():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError, match="Network egress denied"):
            s.connect(("127.0.0.1", 9999))
