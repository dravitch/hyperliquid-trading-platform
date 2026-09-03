from __future__ import annotations

import asyncio
import inspect

import pytest
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid.execution import HyperliquidExecutionClient
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.live.node import TradingNode

from hltrader.venue.read_only_execution import (
    NAUTILUS_HYPERLIQUID_MUTATION_METHODS,
    ReadOnlyCapabilityError,
    ReadOnlyHyperliquidExecClientFactory,
    ReadOnlyHyperliquidExecutionClient,
)

PUBLIC_MUTATIONS = (
    "submit_order",
    "submit_order_list",
    "modify_order",
    "cancel_order",
    "cancel_all_orders",
    "batch_cancel_orders",
)
INTERNAL_MUTATIONS = (
    "_submit_order",
    "_submit_order_list",
    "_modify_order",
    "_cancel_order",
    "_cancel_all_orders",
    "_batch_cancel_orders",
    "_split_outcome",
    "_merge_outcome",
    "_merge_question",
    "_negate_outcome",
)
READ_METHODS = (
    "generate_order_status_report",
    "generate_order_status_reports",
    "generate_fill_reports",
    "generate_position_status_reports",
)


def test_public_command_methods_are_structurally_overridden() -> None:
    for method_name in PUBLIC_MUTATIONS:
        assert getattr(ReadOnlyHyperliquidExecutionClient, method_name) is not getattr(
            HyperliquidExecutionClient, method_name
        )


def test_pinned_adapter_mutation_inventory_has_not_changed() -> None:
    mutation_prefixes = (
        "submit_",
        "modify_",
        "cancel_",
        "batch_cancel_",
        "_submit_",
        "_modify_",
        "_cancel_",
        "_batch_cancel_",
        "_split_",
        "_merge_",
        "_negate_",
    )
    observed = {
        name
        for name in dir(HyperliquidExecutionClient)
        if name != "cancel_pending_tasks"
        and name.startswith(mutation_prefixes)
        and callable(getattr(HyperliquidExecutionClient, name, None))
    }

    assert observed == NAUTILUS_HYPERLIQUID_MUTATION_METHODS


@pytest.mark.parametrize("method_name", INTERNAL_MUTATIONS)
def test_internal_mutation_coroutines_are_structurally_overridden(method_name: str) -> None:
    method = getattr(ReadOnlyHyperliquidExecutionClient, method_name)
    parameters = list(inspect.signature(method).parameters)[1:]
    arguments = [0] * len(parameters)

    with pytest.raises(ReadOnlyCapabilityError, match=method_name):
        asyncio.run(method(None, *arguments))


def test_report_methods_remain_the_official_adapter_implementation() -> None:
    for method_name in READ_METHODS:
        assert getattr(ReadOnlyHyperliquidExecutionClient, method_name) is getattr(
            HyperliquidExecutionClient, method_name
        )


def test_explicit_account_identity_resolves_without_signer() -> None:
    address = "0x0000000000000000000000000000000000000001"

    resolved = nautilus_pyo3.hyperliquid_resolve_execution_account_address(
        private_key=None,
        vault_address=None,
        account_address=address,
        environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
    )

    assert resolved == address


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (
            HyperliquidExecClientConfig(
                environment=nautilus_pyo3.HyperliquidEnvironment.MAINNET,
                account_address="0x0000000000000000000000000000000000000001",
            ),
            "testnet-only",
        ),
        (
            HyperliquidExecClientConfig(
                environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
                account_address="0x0000000000000000000000000000000000000001",
                private_key="secret-is-never-accepted",
            ),
            "credentials are forbidden",
        ),
        (
            HyperliquidExecClientConfig(
                environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
            ),
            "explicit account_address",
        ),
    ],
)
def test_factory_fails_before_dependency_access(config, message: str) -> None:
    with pytest.raises(ReadOnlyCapabilityError, match=message):
        ReadOnlyHyperliquidExecClientFactory.create(
            loop=None,
            name="HYPERLIQUID",
            config=config,
            msgbus=None,
            cache=None,
            clock=None,
        )


def test_factory_rejects_environment_secret(monkeypatch) -> None:
    monkeypatch.setenv("HYPERLIQUID_TESTNET_PK", "never-read")
    config = HyperliquidExecClientConfig(
        environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
        account_address="0x0000000000000000000000000000000000000001",
    )

    with pytest.raises(ReadOnlyCapabilityError, match="HYPERLIQUID_TESTNET_PK"):
        ReadOnlyHyperliquidExecClientFactory.create(
            loop=None,
            name="HYPERLIQUID",
            config=config,
            msgbus=None,
            cache=None,
            clock=None,
        )


def test_trading_node_builds_candidate_wrapper_without_signer(monkeypatch) -> None:
    for name in ReadOnlyHyperliquidExecClientFactory.SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    config = HyperliquidExecClientConfig(
        environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
        account_address="0x0000000000000000000000000000000000000001",
    )
    loop = asyncio.new_event_loop()
    node = TradingNode(TradingNodeConfig(exec_clients={"HYPERLIQUID": config}), loop=loop)
    node.add_exec_client_factory("HYPERLIQUID", ReadOnlyHyperliquidExecClientFactory)

    try:
        node.build()
        clients = node.kernel.exec_engine._clients
        assert len(clients) == 1
        client = next(iter(clients.values()))
        assert isinstance(client, ReadOnlyHyperliquidExecutionClient)
        assert client.blocked_command_counts == {}
    finally:
        node.dispose()
