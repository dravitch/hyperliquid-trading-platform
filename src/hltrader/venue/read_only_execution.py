from __future__ import annotations

import asyncio
import os
from collections import Counter
from decimal import Decimal
from typing import Any, NoReturn

from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid.enums import HyperliquidProductType
from nautilus_trader.adapters.hyperliquid.execution import HyperliquidExecutionClient
from nautilus_trader.adapters.hyperliquid.providers import HyperliquidInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.live.factories import LiveExecClientFactory

NAUTILUS_HYPERLIQUID_MUTATION_METHODS = frozenset(
    {
        "submit_order",
        "submit_order_list",
        "modify_order",
        "cancel_order",
        "cancel_all_orders",
        "batch_cancel_orders",
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
    }
)


class ReadOnlyCapabilityError(RuntimeError):
    """Raised before any venue command can reach the Hyperliquid transport."""


def _blocked(command: str) -> NoReturn:
    raise ReadOnlyCapabilityError(f"venue command blocked in reconciliation-only mode: {command}")


class ReadOnlyHyperliquidExecutionClient(HyperliquidExecutionClient):
    """Hyperliquid report/subscription client with every mutation entry point overridden.

    This class is a candidate capability boundary. It deliberately inherits the official report
    and connection implementation, but both the public command API used by ``ExecutionEngine``
    and the adapter's protected mutation coroutines fail before touching the transport.
    """

    BLOCK_REASON = "reconciliation-only runtime has no venue command capability"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._blocked_command_counts: Counter[str] = Counter()

    @property
    def blocked_command_counts(self) -> dict[str, int]:
        """Return commands rejected at the local capability boundary."""
        return dict(self._blocked_command_counts)

    def _record_block(self, command: str) -> None:
        self._blocked_command_counts[command] += 1

    def submit_order(self, command: Any) -> None:
        self._record_block("submit_order")
        self.generate_order_denied(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.order.client_order_id,
            reason=self.BLOCK_REASON,
            ts_event=self._clock.timestamp_ns(),
        )

    def submit_order_list(self, command: Any) -> None:
        self._record_block("submit_order_list")
        for order in command.order_list.orders:
            self.generate_order_denied(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=self.BLOCK_REASON,
                ts_event=self._clock.timestamp_ns(),
            )

    def modify_order(self, command: Any) -> None:
        self._record_block("modify_order")
        self.generate_order_modify_rejected(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=command.venue_order_id,
            reason=self.BLOCK_REASON,
            ts_event=self._clock.timestamp_ns(),
        )

    def cancel_order(self, command: Any) -> None:
        self._record_block("cancel_order")
        self.generate_order_cancel_rejected(
            strategy_id=command.strategy_id,
            instrument_id=command.instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=command.venue_order_id,
            reason=self.BLOCK_REASON,
            ts_event=self._clock.timestamp_ns(),
        )

    def cancel_all_orders(self, command: Any) -> None:
        self._record_block("cancel_all_orders")
        for order in self._cache.orders_open(
            venue=self.venue,
            instrument_id=command.instrument_id,
            strategy_id=command.strategy_id,
            side=command.order_side,
        ):
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=order.venue_order_id,
                reason=self.BLOCK_REASON,
                ts_event=self._clock.timestamp_ns(),
            )

    def batch_cancel_orders(self, command: Any) -> None:
        self._record_block("batch_cancel_orders")
        for cancel in command.cancels:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=cancel.instrument_id,
                client_order_id=cancel.client_order_id,
                venue_order_id=cancel.venue_order_id,
                reason=self.BLOCK_REASON,
                ts_event=self._clock.timestamp_ns(),
            )

    async def _submit_order(self, command: Any) -> NoReturn:
        del command
        _blocked("_submit_order")

    async def _submit_order_list(self, command: Any) -> NoReturn:
        del command
        _blocked("_submit_order_list")

    async def _modify_order(self, command: Any) -> NoReturn:
        del command
        _blocked("_modify_order")

    async def _cancel_order(self, command: Any) -> NoReturn:
        del command
        _blocked("_cancel_order")

    async def _cancel_all_orders(self, command: Any) -> NoReturn:
        del command
        _blocked("_cancel_all_orders")

    async def _batch_cancel_orders(self, command: Any) -> NoReturn:
        del command
        _blocked("_batch_cancel_orders")

    async def _split_outcome(self, outcome: int, amount: Decimal) -> NoReturn:
        del outcome, amount
        _blocked("_split_outcome")

    async def _merge_outcome(self, outcome: int, amount: Decimal | None = None) -> NoReturn:
        del outcome, amount
        _blocked("_merge_outcome")

    async def _merge_question(self, question: int, amount: Decimal | None = None) -> NoReturn:
        del question, amount
        _blocked("_merge_question")

    async def _negate_outcome(self, question: int, outcome: int, amount: Decimal) -> NoReturn:
        del question, outcome, amount
        _blocked("_negate_outcome")


class ReadOnlyHyperliquidExecClientFactory(LiveExecClientFactory):
    """Construct the candidate wrapper only with an explicit account and no signer."""

    SECRET_ENV_NAMES = (
        "HYPERLIQUID_PK",
        "HYPERLIQUID_TESTNET_PK",
        "HYPERLIQUID_VAULT",
        "HYPERLIQUID_TESTNET_VAULT",
    )

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: HyperliquidExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> ReadOnlyHyperliquidExecutionClient:
        environment = config.environment or nautilus_pyo3.HyperliquidEnvironment.MAINNET
        if environment != nautilus_pyo3.HyperliquidEnvironment.TESTNET:
            raise ReadOnlyCapabilityError("reconciliation-only factory is testnet-only")
        if config.private_key or config.vault_address:
            raise ReadOnlyCapabilityError("signer and vault credentials are forbidden")
        present = [name for name in ReadOnlyHyperliquidExecClientFactory.SECRET_ENV_NAMES if os.getenv(name)]
        if present:
            raise ReadOnlyCapabilityError(
                f"credential environment variables are forbidden: {', '.join(present)}"
            )
        if not config.account_address:
            raise ReadOnlyCapabilityError("explicit account_address is required for private reads")

        # Do not use Nautilus's cached factory helper: a process-global cached client could have
        # been constructed earlier with signing capability.
        client = nautilus_pyo3.HyperliquidHttpClient(
            private_key=None,
            vault_address=None,
            account_address=config.account_address,
            environment=environment,
            proxy_url=config.proxy_url,
            normalize_prices=config.normalize_prices,
            include_builder_attribution=False,
            timeout_secs=config.http_timeout_secs,
        )
        product_types = (
            tuple(HyperliquidProductType(value) for value in config.product_types)
            if config.product_types is not None
            else None
        )
        provider = HyperliquidInstrumentProvider(
            client=client,
            config=config.instrument_provider,
            product_types=product_types,
        )
        account_address = nautilus_pyo3.hyperliquid_resolve_execution_account_address(
            private_key=None,
            vault_address=None,
            account_address=config.account_address,
            environment=environment,
        )
        return ReadOnlyHyperliquidExecutionClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
            account_address=account_address,
        )
