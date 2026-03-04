"""Async client for the Octopus Energy Japan GraphQL API."""

import httpx
from datetime import datetime, timedelta
from typing import Optional

from .models import Account, Consumption, Tariff, Rate


GRAPHQL_URL = "https://api.oejp-kraken.energy/v1/graphql/"


class OctopusClient:
    """
    Async client for Octopus Energy Japan's GraphQL (Kraken) API.

    Example:
        >>> async with OctopusClient(email="user@example.com", password="xxx") as client:
        ...     account = await client.get_account()
        ...     print(f"Balance: ¥{account.balance:.0f}")
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        account: Optional[str] = None,
    ):
        """
        Initialize the Octopus Energy Japan client.

        Args:
            email: Your Octopus account email
            password: Your Octopus account password
            account: Your account number (e.g., A-FB05ED6C). Auto-discovered if not provided.
        """
        self.email = email
        self.password = password
        self.account = account

        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._http = httpx.AsyncClient()
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _get_http(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def _get_token(self) -> str:
        """Get or refresh GraphQL authentication token."""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        http = await self._get_http()

        if self._refresh_token:
            variables = {"input": {"refreshToken": self._refresh_token}}
        elif self.email and self.password:
            variables = {"input": {"email": self.email, "password": self.password}}
        else:
            raise AuthenticationError("Email and password required")

        resp = await http.post(
            GRAPHQL_URL,
            json={
                "query": """
                    mutation obtainKrakenToken($input: ObtainJSONWebTokenInput!) {
                        obtainKrakenToken(input: $input) {
                            token
                            refreshToken
                        }
                    }
                """,
                "variables": variables
            }
        )

        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            raise AuthenticationError(data["errors"][0]["message"])

        token_data = data["data"]["obtainKrakenToken"]
        self._token = token_data["token"]
        if "refreshToken" in token_data:
            self._refresh_token = token_data["refreshToken"]
        self._token_expires = datetime.now() + timedelta(minutes=55)
        return self._token

    async def _graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        """Execute a GraphQL query with authentication."""
        token = await self._get_token()
        http = await self._get_http()

        resp = await http.post(
            GRAPHQL_URL,
            headers={"Authorization": token},
            json={"query": query, "variables": variables or {}}
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            # If we also got data back, treat as partial success
            if data.get("data"):
                import sys
                print(f"GraphQL partial error: {data['errors'][0]['message']}", file=sys.stderr)
                return data["data"]
            raise APIError(data["errors"][0]["message"])

        return data["data"]

    # -------------------------------------------------------------------------
    # Account & Billing
    # -------------------------------------------------------------------------

    async def _ensure_account(self) -> str:
        """Ensure account number is available, auto-discovering if needed."""
        if self.account:
            return self.account

        data = await self._graphql(
            """
            query accountViewer {
                viewer {
                    accounts {
                        number
                    }
                }
            }
            """
        )
        accounts = data.get("viewer", {}).get("accounts", [])
        if accounts:
            self.account = accounts[0]["number"]
            return self.account
        raise ConfigurationError("No account found for this user")

    async def get_account(self) -> Account:
        """
        Get account information including balance.

        Returns:
            Account with balance, name, status and address
        """
        account_number = await self._ensure_account()

        data = await self._graphql(
            """
            query GetAccount($account: String!) {
                account(accountNumber: $account) {
                    balance
                    billingName
                    status
                    properties {
                        address
                    }
                }
            }
            """,
            {"account": account_number}
        )
        acc = data["account"]

        return Account(
            number=account_number,
            balance=acc["balance"],  # Already in yen
            name=acc["billingName"] or "",
            status=acc["status"] or "",
            address=acc["properties"][0]["address"] if acc.get("properties") else ""
        )

    # -------------------------------------------------------------------------
    # Consumption
    # -------------------------------------------------------------------------

    async def get_consumption(
        self,
        periods: int = 48,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> list[Consumption]:
        """
        Get half-hourly electricity consumption.

        Args:
            periods: Number of 30-minute periods (default 48 = 24 hours)
            start: Start datetime (optional, defaults to last 24 hours)
            end: End datetime (optional, defaults to now)

        Returns:
            List of Consumption readings
        """
        account_number = await self._ensure_account()

        if not end:
            end = datetime.now()
        if not start:
            start = end - timedelta(minutes=30 * periods)

        data = await self._graphql(
            """
            query halfHourlyReadings($accountNumber: String!, $fromDatetime: DateTime, $toDatetime: DateTime) {
                account(accountNumber: $accountNumber) {
                    properties {
                        electricitySupplyPoints {
                            status
                            halfHourlyReadings(fromDatetime: $fromDatetime, toDatetime: $toDatetime) {
                                startAt
                                value
                                costEstimate
                            }
                        }
                    }
                }
            }
            """,
            {
                "accountNumber": account_number,
                "fromDatetime": start.isoformat(),
                "toDatetime": end.isoformat()
            }
        )

        readings = []
        properties = data.get("account", {}).get("properties", [])
        for prop in properties:
            for supply_point in prop.get("electricitySupplyPoints", []):
                if supply_point is None:
                    continue
                for r in supply_point.get("halfHourlyReadings", []) or []:
                    try:
                        start_at = datetime.fromisoformat(r["startAt"].replace("Z", "+00:00"))
                        end_at = start_at + timedelta(minutes=30)
                        readings.append(Consumption(
                            start=start_at,
                            end=end_at,
                            kwh=float(r["value"]),
                            cost_estimate=float(r.get("costEstimate") or 0)
                        ))
                    except (ValueError, KeyError, TypeError):
                        continue

        return sorted(readings, key=lambda c: c.start)

    async def get_daily_usage(self, days: int = 7) -> dict[str, float]:
        """
        Get daily electricity consumption totals.

        Args:
            days: Number of days to fetch

        Returns:
            Dict mapping date strings to kWh totals
        """
        consumption = await self.get_consumption(periods=days * 48)
        daily: dict[str, float] = {}
        for c in consumption:
            day = c.start.strftime("%Y-%m-%d")
            daily[day] = daily.get(day, 0) + c.kwh
        return daily

    # -------------------------------------------------------------------------
    # Tariff
    # -------------------------------------------------------------------------

    async def get_tariff(self) -> Optional[Tariff]:
        """
        Get current electricity tariff details.

        Returns:
            Tariff with rates, or None if not found
        """
        account_number = await self._ensure_account()

        data = await self._graphql(
            """
            query GetTariffJapan($account: String!) {
                account(accountNumber: $account) {
                    properties {
                        electricitySupplyPoints {
                            agreements {
                                validFrom
                                validTo
                                product {
                                    ... on ProductInterface {
                                        code
                                        displayName
                                        standingChargePricePerDay
                                        standingChargeUnitType
                                        fuelCostAdjustment {
                                            pricePerUnitIncTax
                                        }
                                        renewableEnergyLevy {
                                            pricePerUnitIncTax
                                        }
                                    }
                                    ... on ElectricitySingleStepProduct {
                                        consumptionCharges {
                                            pricePerUnitIncTax
                                            band
                                            timeOfUse
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """,
            {"account": account_number}
        )

        properties = data.get("account", {}).get("properties", [])
        for prop in properties:
            for sp in prop.get("electricitySupplyPoints", []):
                if sp is None:
                    continue
                for agreement in sp.get("agreements", []):
                    product = agreement.get("product")
                    if not product:
                        continue

                    code = product.get("code", "")
                    display_name = product.get("displayName", "Unknown")
                    standing_charge = float(product.get("standingChargePricePerDay") or 0)

                    # Fuel cost adjustment and renewable levy (per kWh)
                    fca = float(product.get("fuelCostAdjustment", {}).get("pricePerUnitIncTax") or 0)
                    rel = float(product.get("renewableEnergyLevy", {}).get("pricePerUnitIncTax") or 0)

                    # Consumption charges (unit rate per kWh)
                    rates = {}
                    unit_rate = None
                    for charge in product.get("consumptionCharges", []) or []:
                        rate = float(charge.get("pricePerUnitIncTax") or 0)
                        band = charge.get("band", "")
                        tou = charge.get("timeOfUse", "")
                        rates[band or tou or "standard"] = rate
                        if unit_rate is None or band == "standard":
                            unit_rate = rate

                    # Total effective rate = unit rate + fuel cost adj + renewable levy
                    effective_rate = (unit_rate or 0) + fca + rel

                    return Tariff(
                        name=display_name,
                        product_code=code,
                        standing_charge=standing_charge,
                        rates={"standard": effective_rate, "base": unit_rate or 0, "fca": fca, "rel": rel, **rates},
                        peak_rate=effective_rate,
                    )

        return None

    def get_current_rate(self, tariff: Tariff) -> Rate:
        """
        Get current rate.

        Args:
            tariff: Tariff object with rate info

        Returns:
            Rate with current pricing
        """
        now = datetime.now()
        rate = tariff.peak_rate or tariff.rates.get("standard", 0)
        return Rate(
            rate=rate,
            period_end=now.replace(hour=23, minute=59, second=59) + timedelta(seconds=1),
        )


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class OctopusError(Exception):
    """Base exception for Open Octopus errors."""

class AuthenticationError(OctopusError):
    """Authentication failed."""

class APIError(OctopusError):
    """API request failed."""

class ConfigurationError(OctopusError):
    """Missing or invalid configuration."""
