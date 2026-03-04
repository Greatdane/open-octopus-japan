"""
Open Octopus Japan - Python client for the Octopus Energy Japan API.

Supports:
- Account balance and tariff info
- Half-hourly electricity consumption
- macOS menu bar app
- Claude AI agent for natural language queries

Example:
    >>> from open_octopus import OctopusClient
    >>>
    >>> async with OctopusClient(email="user@example.com", password="xxx") as client:
    ...     account = await client.get_account()
    ...     print(f"Balance: ¥{account.balance:.0f}")
"""

__version__ = "0.4.0"

from .client import (
    OctopusClient,
    OctopusError,
    AuthenticationError,
    APIError,
    ConfigurationError,
)
from .models import (
    Account,
    Consumption,
    Tariff,
    Rate,
)

# Optional imports for extras
try:
    from .agent import OctopusAgent
except ImportError:
    OctopusAgent = None

try:
    from .menubar import OctopusMenuBar
except ImportError:
    OctopusMenuBar = None

__all__ = [
    # Client
    "OctopusClient",
    # Exceptions
    "OctopusError",
    "AuthenticationError",
    "APIError",
    "ConfigurationError",
    # Models
    "Account",
    "Consumption",
    "Tariff",
    "Rate",
    # Optional
    "OctopusAgent",
    "OctopusMenuBar",
]
