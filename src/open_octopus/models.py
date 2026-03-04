"""Data models for Open Octopus Japan API responses."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Account:
    """Octopus Energy Japan account information."""
    number: str
    balance: float  # JPY
    name: str
    status: str
    address: str


@dataclass
class Consumption:
    """Half-hourly electricity consumption reading."""
    start: datetime
    end: datetime
    kwh: float
    cost_estimate: Optional[float] = None  # Estimated cost in yen


@dataclass
class Tariff:
    """Electricity tariff details."""
    name: str
    product_code: str
    standing_charge: float  # yen/day
    rates: dict[str, float]  # rate_name -> yen/kWh
    peak_rate: Optional[float] = None  # yen/kWh (effective rate)


@dataclass
class Rate:
    """Current electricity rate."""
    rate: float  # yen/kWh
    period_end: datetime
