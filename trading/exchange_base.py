"""Abstract exchange client interface for the MEXC client."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ExchangeClient(ABC):
    """Common interface for spot exchange clients."""

    @abstractmethod
    def has_credentials(self) -> bool:
        ...

    @abstractmethod
    def validate_credentials(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        ...

    @abstractmethod
    def get_all_tickers(self) -> dict[str, float]:
        ...

    @abstractmethod
    def get_account_balance(self) -> list[dict]:
        ...

    @abstractmethod
    def get_symbol_precision(self, symbol: str) -> tuple[int, int]:
        """Return (price_places, qty_places)"""
        ...

    @abstractmethod
    def get_min_notional(self, symbol: str) -> float:
        ...

    @abstractmethod
    def get_min_base_qty(self, symbol: str) -> float:
        ...

    @abstractmethod
    def place_market_sell(self, symbol: str, qty: float, qty_places: int = 6) -> dict:
        ...

    @abstractmethod
    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> dict:
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> dict:
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[dict]:
        ...

    @abstractmethod
    def cancel_symbol_orders_batch(self, symbol: str) -> bool:
        ...

    @abstractmethod
    def close_all_at_market(self, symbols: list[str]) -> dict:
        ...

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        ...
