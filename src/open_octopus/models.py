"""Data models for Open Octopus Japan API responses."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Account:
    """Octopus Energy Japan account information."""
    number: str
    balance: float  # JPY (negative = credit per API convention)
    name: str
    status: str
    address: str


@dataclass
class Consumption:
    """Half-hourly electricity consumption reading."""
    start: datetime
    end: datetime
    kwh: float
    cost_estimate: Optional[float] = None  # Estimated cost in yen (from API)
    consumption_step: Optional[int] = None  # Tiered pricing step
    consumption_rate_band: Optional[str] = None  # Rate band name


@dataclass
class Tariff:
    """Electricity tariff details."""
    name: str
    product_code: str
    standing_charge: float  # yen/day
    rates: dict[str, float] = field(default_factory=dict)  # rate_name -> yen/kWh
    peak_rate: Optional[float] = None  # yen/kWh (effective rate)


@dataclass
class Rate:
    """Current electricity rate."""
    rate: float  # yen/kWh
    period_end: datetime


@dataclass
class SupplyPoint:
    """Electricity supply point details."""
    spin: str  # Supply Point Identification Number
    status: str
    meter_serial: Optional[str] = None
    agreements: list[dict] = field(default_factory=list)


@dataclass
class PostalArea:
    """Japanese postal area information."""
    postcode: str
    prefecture: str
    city: str
    area: str


# -------------------------------------------------------------------------
# New models for extended API coverage
# -------------------------------------------------------------------------

@dataclass
class Agreement:
    """Electricity supply agreement with product and validity info."""
    id: int
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    product_code: str = ""
    product_name: str = ""


@dataclass
class Product:
    """An available electricity product/tariff plan."""
    code: str
    display_name: str
    description: str = ""
    standing_charge: float = 0.0  # yen/day
    is_available: bool = True
    rates: dict[str, float] = field(default_factory=dict)  # band -> yen/kWh


@dataclass
class LoyaltyPoints:
    """Loyalty/rewards points balance."""
    balance: int = 0
    ledger_entries: list[dict] = field(default_factory=list)


@dataclass
class PlannedDispatch:
    """Planned smart device dispatch window."""
    start: datetime
    end: datetime
    delta: Optional[float] = None  # kW delta
    source: str = ""
