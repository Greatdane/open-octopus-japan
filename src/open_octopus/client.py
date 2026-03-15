"""Async client for the Octopus Energy Japan GraphQL API."""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from .models import (
    Account,
    Agreement,
    Consumption,
    LoyaltyPoints,
    PlannedDispatch,
    PostalArea,
    Product,
    Rate,
    SupplyPoint,
    Tariff,
)

logger = logging.getLogger("open_octopus")

GRAPHQL_URL = "https://api.oejp-kraken.energy/v1/graphql/"

# Token expires after 60 minutes; refresh 5 minutes early
_TOKEN_TTL_MINUTES = 55
# Refresh token expires after 7 days; refresh 1 hour early
_REFRESH_TTL_MINUTES = 7 * 24 * 60 - 60


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
        self.email = email
        self.password = password
        self.account = account

        self._token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._refresh_expires: Optional[datetime] = None
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "OctopusClient":
        self._http = httpx.AsyncClient()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    async def _get_token(self) -> str:
        """Get or refresh GraphQL authentication token."""
        if self._token and self._token_expires and datetime.now() < self._token_expires:
            return self._token

        http = await self._get_http()

        # Try refresh token first (if still valid), fall back to credentials
        if self._refresh_token and self._refresh_expires and datetime.now() < self._refresh_expires:
            variables = {"input": {"refreshToken": self._refresh_token}}
            logger.debug("Refreshing token via refresh token")
        elif self.email and self.password:
            variables = {"input": {"email": self.email, "password": self.password}}
            logger.debug("Authenticating with email/password")
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
                            refreshExpiresIn
                        }
                    }
                """,
                "variables": variables
            }
        )

        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            # If refresh token failed, retry with credentials
            if self._refresh_token and self.email and self.password:
                logger.debug("Refresh token failed, retrying with credentials")
                self._refresh_token = None
                self._refresh_expires = None
                return await self._get_token()
            raise AuthenticationError(data["errors"][0]["message"])

        token_data = data["data"]["obtainKrakenToken"]
        self._token = token_data["token"]
        self._token_expires = datetime.now() + timedelta(minutes=_TOKEN_TTL_MINUTES)

        if token_data.get("refreshToken"):
            self._refresh_token = token_data["refreshToken"]
            refresh_seconds = token_data.get("refreshExpiresIn")
            if refresh_seconds:
                self._refresh_expires = datetime.now() + timedelta(seconds=int(refresh_seconds))
            else:
                self._refresh_expires = datetime.now() + timedelta(minutes=_REFRESH_TTL_MINUTES)

        return self._token

    async def _graphql(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Execute an authenticated GraphQL query."""
        token = await self._get_token()
        http = await self._get_http()

        resp = await http.post(
            GRAPHQL_URL,
            headers={"Authorization": token},
            json={"query": query, "variables": variables or {}}
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        if "errors" in data:
            if data.get("data"):
                logger.debug("GraphQL partial error: %s", data["errors"][0]["message"])
                result: dict[str, Any] = data["data"]
                return result
            raise APIError(data["errors"][0]["message"])

        result = data["data"]
        return result

    async def _graphql_public(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Execute an unauthenticated GraphQL query."""
        http = await self._get_http()

        resp = await http.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}}
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        if "errors" in data:
            raise APIError(data["errors"][0]["message"])

        result: dict[str, Any] = data["data"]
        return result

    async def _graphql_safe(self, query: str, variables: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Execute an authenticated GraphQL query, returning None on any error.

        Use this for speculative queries that may not exist on the Japan API.
        """
        try:
            return await self._graphql(query, variables)
        except (httpx.HTTPStatusError, APIError) as e:
            logger.debug("GraphQL query failed (safe): %s", e)
            return None

    # -------------------------------------------------------------------------
    # Account
    # -------------------------------------------------------------------------

    async def _ensure_account(self) -> str:
        """Ensure account number is available, auto-discovering if needed."""
        if self.account:
            return self.account

        data = await self._graphql("""
            query accountViewer {
                viewer {
                    accounts {
                        number
                    }
                }
            }
        """)
        accounts = data.get("viewer", {}).get("accounts", [])
        if accounts:
            self.account = accounts[0]["number"]
            return self.account
        raise ConfigurationError("No account found for this user")

    async def get_account(self) -> Account:
        """Get account information including balance."""
        account_number = await self._ensure_account()

        data = await self._graphql("""
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
        """, {"account": account_number})

        acc = data["account"]
        return Account(
            number=account_number,
            balance=acc["balance"],
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

        Tries the halfHourlyReadings endpoint first (used by official Octopus
        Energy Japan examples, returns costEstimate). Falls back to the
        measurements endpoint if halfHourlyReadings fails.

        Args:
            periods: Number of 30-minute periods (default 48 = 24 hours)
            start: Start datetime (optional, defaults to last N periods)
            end: End datetime (optional, defaults to now)

        Returns:
            List of Consumption readings sorted by start time
        """
        if not end:
            end = datetime.now()
        if not start:
            start = end - timedelta(minutes=30 * periods)

        try:
            readings = await self._get_consumption_hh(start, end)
            if readings:
                return readings
            logger.debug("halfHourlyReadings returned empty, trying measurements")
        except (APIError, KeyError, TypeError, AttributeError) as e:
            logger.debug("halfHourlyReadings failed (%s), trying measurements", e)

        return await self._get_consumption_measurements(start, end)

    async def _get_consumption_hh(
        self, start: datetime, end: datetime
    ) -> list[Consumption]:
        """Get consumption via halfHourlyReadings endpoint."""
        account_number = await self._ensure_account()

        data = await self._graphql("""
            query halfHourlyReadings(
                $accountNumber: String!,
                $fromDatetime: DateTime,
                $toDatetime: DateTime
            ) {
                account(accountNumber: $accountNumber) {
                    properties {
                        electricitySupplyPoints {
                            halfHourlyReadings(
                                fromDatetime: $fromDatetime,
                                toDatetime: $toDatetime
                            ) {
                                startAt
                                endAt
                                value
                                costEstimate
                                consumptionStep
                                consumptionRateBand
                            }
                        }
                    }
                }
            }
        """, {
            "accountNumber": account_number,
            "fromDatetime": start.isoformat(),
            "toDatetime": end.isoformat(),
        })

        readings = []
        account_data = data.get("account") or {}
        for prop in account_data.get("properties") or []:
            for sp in prop.get("electricitySupplyPoints") or []:
                for r in sp.get("halfHourlyReadings") or []:
                    if not r.get("startAt"):
                        continue
                    try:
                        cost = r.get("costEstimate")
                        readings.append(Consumption(
                            start=datetime.fromisoformat(r["startAt"]),
                            end=datetime.fromisoformat(r["endAt"]),
                            kwh=float(r["value"]),
                            cost_estimate=float(cost) if cost is not None else None,
                            consumption_step=r.get("consumptionStep"),
                            consumption_rate_band=r.get("consumptionRateBand"),
                        ))
                    except (ValueError, KeyError, TypeError):
                        continue

        return sorted(readings, key=lambda c: c.start)

    async def _get_consumption_measurements(
        self, start: datetime, end: datetime
    ) -> list[Consumption]:
        """Get consumption via measurements endpoint (fallback)."""
        account_number = await self._ensure_account()

        readings = []
        cursor = None
        has_next = True

        while has_next:
            variables: dict = {
                "account": account_number,
                "startAt": start.isoformat(),
                "endAt": end.isoformat()
            }
            if cursor:
                variables["after"] = cursor

            data = await self._graphql("""
                query getMeasurements(
                    $account: String!,
                    $startAt: DateTime,
                    $endAt: DateTime,
                    $after: String
                ) {
                    account(accountNumber: $account) {
                        properties {
                            measurements(
                                startAt: $startAt,
                                endAt: $endAt,
                                first: 100,
                                after: $after,
                                utilityFilters: [{electricityFilters: {}}]
                            ) {
                                edges {
                                    node {
                                        ... on IntervalMeasurementType {
                                            startAt
                                            endAt
                                            value
                                            unit
                                        }
                                    }
                                    cursor
                                }
                                pageInfo {
                                    hasNextPage
                                }
                            }
                        }
                    }
                }
            """, variables)

            if not data or not data.get("account"):
                break

            properties = (data.get("account") or {}).get("properties") or []
            if not properties:
                break

            for prop in properties:
                measurements = prop.get("measurements") or {}
                for edge in measurements.get("edges", []):
                    node = edge.get("node") or {}
                    if not node.get("startAt"):
                        continue
                    try:
                        readings.append(Consumption(
                            start=datetime.fromisoformat(node["startAt"]),
                            end=datetime.fromisoformat(node["endAt"]),
                            kwh=float(node["value"]),
                        ))
                    except (ValueError, KeyError, TypeError):
                        continue
                    cursor = edge.get("cursor")

                page_info = measurements.get("pageInfo") or {}
                has_next = page_info.get("hasNextPage", False)

        return sorted(readings, key=lambda c: c.start)

    async def get_daily_usage(
        self, days: int = 7, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> dict[str, float]:
        """
        Get daily electricity consumption totals.

        Args:
            days: Number of days to fetch (used if start/end not provided)
            start: Start datetime (optional)
            end: End datetime (optional)

        Returns:
            Dict mapping date strings to kWh totals
        """
        if start and end:
            consumption = await self.get_consumption(start=start, end=end)
        else:
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
        """Get current electricity tariff details."""
        account_number = await self._ensure_account()

        data = await self._graphql("""
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
                                    ... on ElectricitySteppedProduct {
                                        consumptionCharges {
                                            pricePerUnitIncTax
                                            band
                                            stepStart
                                            stepEnd
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """, {"account": account_number})

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

                    fca = float((product.get("fuelCostAdjustment") or {}).get("pricePerUnitIncTax") or 0)
                    rel = float((product.get("renewableEnergyLevy") or {}).get("pricePerUnitIncTax") or 0)

                    # Parse consumption charges (works for both single-step and multi-step)
                    rates: dict[str, float] = {}
                    unit_rate = None
                    for charge in product.get("consumptionCharges") or []:
                        rate = float(charge.get("pricePerUnitIncTax") or 0)
                        band = charge.get("band", "")
                        tou = charge.get("timeOfUse", "")
                        step_start = charge.get("stepStart")
                        step_end = charge.get("stepEnd")

                        # Build a descriptive label for tiered rates
                        if step_start is not None or step_end is not None:
                            s = int(step_start) if step_start is not None else 0
                            e = f"{int(step_end)}" if step_end is not None else "∞"
                            label = f"{s}-{e}kWh"
                        else:
                            label = band or tou or "standard"

                        rates[label] = rate
                        # Use the highest-volume tier as the representative rate
                        if unit_rate is None or rate > (unit_rate or 0):
                            unit_rate = rate

                    effective_rate = (unit_rate or 0) + fca + rel

                    return Tariff(
                        name=display_name,
                        product_code=code,
                        standing_charge=standing_charge,
                        rates={
                            "standard": effective_rate,
                            "base": unit_rate or 0,
                            "fca": fca,
                            "rel": rel,
                            **rates,
                        },
                        peak_rate=effective_rate,
                    )

        return None

    def get_current_rate(self, tariff: Tariff) -> Rate:
        """Get current rate from tariff info."""
        now = datetime.now()
        rate = tariff.peak_rate or tariff.rates.get("standard", 0)
        return Rate(
            rate=rate,
            period_end=now.replace(hour=23, minute=59, second=59) + timedelta(seconds=1),
        )

    # -------------------------------------------------------------------------
    # Supply Points
    # -------------------------------------------------------------------------

    async def get_supply_points(self) -> list[SupplyPoint]:
        """Get electricity supply point details for the account."""
        account_number = await self._ensure_account()

        data = await self._graphql("""
            query GetSupplyPoints($account: String!) {
                account(accountNumber: $account) {
                    properties {
                        electricitySupplyPoints {
                            spin
                            status
                            meters {
                                serialNumber
                            }
                            agreements {
                                id
                                validFrom
                                validTo
                                product {
                                    ... on ProductInterface {
                                        code
                                        displayName
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """, {"account": account_number})

        supply_points = []
        for prop in data.get("account", {}).get("properties", []):
            for sp in prop.get("electricitySupplyPoints", []):
                if sp is None:
                    continue
                meters = sp.get("meters") or []
                meter_serial = meters[0]["serialNumber"] if meters else None
                agreements = []
                for agr in sp.get("agreements", []) or []:
                    product = agr.get("product") or {}
                    agreements.append({
                        "id": agr.get("id"),
                        "valid_from": agr.get("validFrom"),
                        "valid_to": agr.get("validTo"),
                        "product_code": product.get("code"),
                        "product_name": product.get("displayName"),
                    })
                supply_points.append(SupplyPoint(
                    spin=sp.get("spin", ""),
                    status=sp.get("status", ""),
                    meter_serial=meter_serial,
                    agreements=agreements,
                ))

        return supply_points

    # -------------------------------------------------------------------------
    # Public Queries (no auth required)
    # -------------------------------------------------------------------------

    async def get_postal_areas(self, postcode: str) -> list[PostalArea]:
        """
        Look up area information by Japanese postcode. No authentication required.

        Args:
            postcode: Japanese postcode (e.g., "916-0045")

        Returns:
            List of matching postal areas
        """
        data = await self._graphql_public("""
            query postalAreas($postcode: String!) {
                postalAreas(postcode: $postcode) {
                    postcode
                    prefecture
                    city
                    area
                }
            }
        """, {"postcode": postcode})

        return [
            PostalArea(
                postcode=area["postcode"],
                prefecture=area["prefecture"],
                city=area["city"],
                area=area["area"],
            )
            for area in data.get("postalAreas", [])
        ]

    # -------------------------------------------------------------------------
    # Agreements
    # -------------------------------------------------------------------------

    async def get_agreements(self) -> list[Agreement]:
        """Get all electricity supply agreements for the account."""
        account_number = await self._ensure_account()

        data = await self._graphql("""
            query GetAgreements($account: String!) {
                account(accountNumber: $account) {
                    properties {
                        electricitySupplyPoints {
                            agreements {
                                id
                                validFrom
                                validTo
                                product {
                                    ... on ProductInterface {
                                        code
                                        displayName
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """, {"account": account_number})

        agreements = []
        for prop in (data.get("account") or {}).get("properties") or []:
            for sp in prop.get("electricitySupplyPoints") or []:
                if sp is None:
                    continue
                for agr in sp.get("agreements") or []:
                    product = agr.get("product") or {}
                    valid_from = None
                    valid_to = None
                    try:
                        if agr.get("validFrom"):
                            valid_from = datetime.fromisoformat(agr["validFrom"])
                        if agr.get("validTo"):
                            valid_to = datetime.fromisoformat(agr["validTo"])
                    except (ValueError, TypeError):
                        pass
                    agreements.append(Agreement(
                        id=agr.get("id", 0),
                        valid_from=valid_from,
                        valid_to=valid_to,
                        product_code=product.get("code", ""),
                        product_name=product.get("displayName", ""),
                    ))

        return agreements

    # -------------------------------------------------------------------------
    # Available Products
    # -------------------------------------------------------------------------

    async def get_available_products(self, postcode: Optional[str] = None) -> list[Product]:
        """
        Browse available electricity products/plans.

        Args:
            postcode: Optional Japanese postcode to filter by region

        Returns:
            List of available products. Returns empty list if query not supported.
        """
        try:
            if postcode:
                data = await self._graphql_public("""
                    query GetProducts($postcode: String!) {
                        productsForPostalArea(postcode: $postcode) {
                            code
                            displayName
                            description
                            standingChargePricePerDay
                            consumptionCharges {
                                pricePerUnitIncTax
                                band
                            }
                        }
                    }
                """, {"postcode": postcode})
                products_data = data.get("productsForPostalArea") or []
            else:
                data = await self._graphql_public("""
                    query GetProducts {
                        products {
                            code
                            displayName
                            description
                            standingChargePricePerDay
                            consumptionCharges {
                                pricePerUnitIncTax
                                band
                            }
                        }
                    }
                """)
                products_data = data.get("products") or []

            products = []
            for p in products_data:
                rates: dict[str, float] = {}
                for charge in p.get("consumptionCharges") or []:
                    band = charge.get("band", "standard")
                    rates[band] = float(charge.get("pricePerUnitIncTax") or 0)
                products.append(Product(
                    code=p.get("code", ""),
                    display_name=p.get("displayName", ""),
                    description=p.get("description", ""),
                    standing_charge=float(p.get("standingChargePricePerDay") or 0),
                    rates=rates,
                ))
            return products
        except (httpx.HTTPStatusError, APIError, KeyError, TypeError, AttributeError) as e:
            logger.debug("get_available_products failed: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Billing
    # -------------------------------------------------------------------------

    async def get_billing(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get recent billing transactions.

        Args:
            limit: Number of transactions to fetch (default 10)

        Returns:
            List of transaction dicts. Returns empty list if not supported.
        """
        try:
            account_number = await self._ensure_account()

            data = await self._graphql_safe("""
                query GetBilling($account: String!, $first: Int) {
                    account(accountNumber: $account) {
                        billingTransactions(first: $first) {
                            edges {
                                node {
                                    id
                                    postedDate
                                    amounts {
                                        net
                                    }
                                    transactionType
                                    title
                                    isReversed
                                }
                            }
                        }
                    }
                }
            """, {"account": account_number, "first": limit})

            if not data:
                return []

            transactions = []
            billing = (data.get("account") or {}).get("billingTransactions") or {}
            for edge in billing.get("edges") or []:
                node = edge.get("node") or {}
                amounts = node.get("amounts") or {}
                transactions.append({
                    "id": node.get("id", ""),
                    "posted_date": node.get("postedDate", ""),
                    "amount": float(amounts.get("net") or 0),
                    "type": node.get("transactionType", ""),
                    "title": node.get("title", ""),
                    "is_reversed": node.get("isReversed", False),
                })
            return transactions
        except (KeyError, TypeError, AttributeError) as e:
            logger.debug("get_billing failed: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Loyalty Points (may not be available)
    # -------------------------------------------------------------------------

    async def get_loyalty_points(self) -> Optional[LoyaltyPoints]:
        """
        Get loyalty/rewards points balance.

        Returns None if loyalty program is not available on this account.
        """
        try:
            account_number = await self._ensure_account()

            data = await self._graphql_safe("""
                query GetLoyalty($account: String!) {
                    account(accountNumber: $account) {
                        loyaltyPointLedgers {
                            balanceCarriedForward
                            entries {
                                value
                                balanceCarriedForward
                                reasonCode
                            }
                        }
                    }
                }
            """, {"account": account_number})

            if not data:
                return None

            ledgers = (data.get("account") or {}).get("loyaltyPointLedgers") or []
            if not ledgers:
                return None

            ledger = ledgers[0] if isinstance(ledgers, list) else ledgers
            balance = int(ledger.get("balanceCarriedForward") or 0)
            entries = []
            for entry in ledger.get("entries") or []:
                entries.append({
                    "value": entry.get("value", 0),
                    "balance": entry.get("balanceCarriedForward", 0),
                    "reason": entry.get("reasonCode", ""),
                })

            return LoyaltyPoints(balance=balance, ledger_entries=entries)
        except (APIError, KeyError, TypeError, AttributeError) as e:
            logger.debug("get_loyalty_points not available: %s", e)
            return None

    # -------------------------------------------------------------------------
    # Planned Dispatches (may not be available)
    # -------------------------------------------------------------------------

    async def get_planned_dispatches(self) -> list[PlannedDispatch]:
        """
        Get planned smart device dispatch windows.

        Returns empty list if dispatches are not available on this account.
        """
        try:
            account_number = await self._ensure_account()

            data = await self._graphql_safe("""
                query GetDispatches($account: String!) {
                    plannedDispatches(accountNumber: $account) {
                        startDt
                        endDt
                        delta
                        meta {
                            source
                        }
                    }
                }
            """, {"account": account_number})

            if not data:
                return []

            dispatches = []
            for d in data.get("plannedDispatches") or []:
                try:
                    dispatches.append(PlannedDispatch(
                        start=datetime.fromisoformat(d["startDt"]),
                        end=datetime.fromisoformat(d["endDt"]),
                        delta=float(d["delta"]) if d.get("delta") is not None else None,
                        source=(d.get("meta") or {}).get("source", ""),
                    ))
                except (ValueError, KeyError, TypeError):
                    continue
            return dispatches
        except (APIError, KeyError, TypeError, AttributeError) as e:
            logger.debug("get_planned_dispatches not available: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Communication Preferences (may not be available)
    # -------------------------------------------------------------------------

    async def get_communication_preferences(self) -> dict[str, Any]:
        """
        Get communication/notification preferences.

        Returns empty dict if not available.
        """
        try:
            data = await self._graphql_safe("""
                query GetPreferences {
                    viewer {
                        preferences {
                            isOptedInToClientMessages
                            isOptedInToOfferMessages
                            isOptedInToRecommendedMessages
                            isOptedInToUpdateMessages
                            isOptedInToThirdPartyMessages
                        }
                    }
                }
            """)

            if not data:
                return {}

            prefs = (data.get("viewer") or {}).get("preferences") or {}
            return dict(prefs)
        except (APIError, KeyError, TypeError, AttributeError) as e:
            logger.debug("get_communication_preferences not available: %s", e)
            return {}

    # -------------------------------------------------------------------------
    # Mutations (client-only, not exposed in CLI)
    # -------------------------------------------------------------------------

    async def initiate_product_switch(self, product_code: str) -> dict[str, Any]:
        """
        Initiate a switch to a different electricity product.

        WARNING: This modifies your account. Use with care.

        Args:
            product_code: The product code to switch to

        Returns:
            API response dict
        """
        account_number = await self._ensure_account()

        data = await self._graphql("""
            mutation SwitchProduct($input: InitiateProductSwitchInput!) {
                initiateProductSwitch(input: $input) {
                    switchDate
                    errors {
                        message
                    }
                }
            }
        """, {"input": {"accountNumber": account_number, "productCode": product_code}})

        return data.get("initiateProductSwitch") or {}

    async def initiate_amperage_change(self, amperage: int) -> dict[str, Any]:
        """
        Request an amperage change for your electricity supply (Japan-specific).

        WARNING: This modifies your account. Use with care.

        Args:
            amperage: Requested amperage (e.g., 30, 40, 50, 60)

        Returns:
            API response dict
        """
        account_number = await self._ensure_account()

        data = await self._graphql("""
            mutation ChangeAmperage($input: InitiateAmperageChangeInput!) {
                initiateAmperageChange(input: $input) {
                    errors {
                        message
                    }
                }
            }
        """, {"input": {"accountNumber": account_number, "requestedAmperage": amperage}})

        return data.get("initiateAmperageChange") or {}


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
