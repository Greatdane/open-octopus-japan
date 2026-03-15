"""Tests for OctopusClient with mocked GraphQL responses."""

from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from open_octopus.client import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    OctopusClient,
)


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "https://api.oejp-kraken.energy/v1/graphql/"),
    )


# ---- Auth token response used by most tests ----
AUTH_RESPONSE = _mock_response({
    "data": {
        "obtainKrakenToken": {
            "token": "test-jwt-token",
            "refreshToken": "test-refresh-token",
            "refreshExpiresIn": 604800,
        }
    }
})


# ---- Helpers ----

def _make_client(account: str = "A-TEST1234") -> OctopusClient:
    return OctopusClient(email="test@example.com", password="pass", account=account)


# =============================================================================
# Authentication
# =============================================================================

@pytest.mark.asyncio
async def test_auth_success():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.return_value = AUTH_RESPONSE
    client._http = mock_http

    token = await client._get_token()
    assert token == "test-jwt-token"
    assert client._refresh_token == "test-refresh-token"
    assert client._refresh_expires is not None


@pytest.mark.asyncio
async def test_auth_failure():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.return_value = _mock_response({
        "errors": [{"message": "Invalid credentials"}]
    })
    client._http = mock_http

    with pytest.raises(AuthenticationError, match="Invalid credentials"):
        await client._get_token()


@pytest.mark.asyncio
async def test_auth_missing_credentials():
    client = OctopusClient()
    with pytest.raises(AuthenticationError, match="Email and password required"):
        await client._get_token()


# =============================================================================
# Account
# =============================================================================

@pytest.mark.asyncio
async def test_get_account():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "balance": -1500,
                    "billingName": "田中太郎",
                    "status": "ACTIVE",
                    "properties": [{"address": "東京都渋谷区1-2-3"}],
                }
            }
        }),
    ]
    client._http = mock_http

    acc = await client.get_account()
    assert acc.number == "A-TEST1234"
    assert acc.balance == -1500
    assert acc.name == "田中太郎"
    assert acc.status == "ACTIVE"
    assert acc.address == "東京都渋谷区1-2-3"


@pytest.mark.asyncio
async def test_ensure_account_auto_discovery():
    client = OctopusClient(email="test@example.com", password="pass")
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "viewer": {
                    "accounts": [{"number": "A-DISCOVERED"}]
                }
            }
        }),
    ]
    client._http = mock_http

    account_number = await client._ensure_account()
    assert account_number == "A-DISCOVERED"
    assert client.account == "A-DISCOVERED"


@pytest.mark.asyncio
async def test_ensure_account_no_accounts():
    client = OctopusClient(email="test@example.com", password="pass")
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({"data": {"viewer": {"accounts": []}}}),
    ]
    client._http = mock_http

    with pytest.raises(ConfigurationError, match="No account found"):
        await client._ensure_account()


# =============================================================================
# Consumption (halfHourlyReadings)
# =============================================================================

@pytest.mark.asyncio
async def test_get_consumption_hh():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "electricitySupplyPoints": [{
                            "halfHourlyReadings": [
                                {
                                    "startAt": "2024-01-01T00:00:00+09:00",
                                    "endAt": "2024-01-01T00:30:00+09:00",
                                    "value": "0.5",
                                    "costEstimate": "15.25",
                                    "consumptionStep": 1,
                                    "consumptionRateBand": "standard",
                                },
                                {
                                    "startAt": "2024-01-01T00:30:00+09:00",
                                    "endAt": "2024-01-01T01:00:00+09:00",
                                    "value": "0.3",
                                    "costEstimate": "9.15",
                                    "consumptionStep": 1,
                                    "consumptionRateBand": "standard",
                                },
                            ]
                        }]
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    readings = await client.get_consumption(
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 1, 1, 0),
    )
    assert len(readings) == 2
    assert readings[0].kwh == 0.5
    assert readings[0].cost_estimate == 15.25
    assert readings[0].consumption_step == 1
    assert readings[0].consumption_rate_band == "standard"
    assert readings[1].kwh == 0.3


# =============================================================================
# Consumption (measurements fallback)
# =============================================================================

@pytest.mark.asyncio
async def test_get_consumption_falls_back_to_measurements():
    """When halfHourlyReadings fails, falls back to measurements."""
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        # halfHourlyReadings fails
        _mock_response({"errors": [{"message": "Field not found"}]}),
        # measurements succeeds (token is cached, no re-auth needed)
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "measurements": {
                            "edges": [{
                                "node": {
                                    "startAt": "2024-01-01T00:00:00+09:00",
                                    "endAt": "2024-01-01T00:30:00+09:00",
                                    "value": "0.5",
                                    "unit": "kWh",
                                },
                                "cursor": "abc123",
                            }],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    readings = await client.get_consumption(
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 1, 1, 0),
    )
    assert len(readings) == 1
    assert readings[0].kwh == 0.5
    assert readings[0].cost_estimate is None  # measurements doesn't return cost


# =============================================================================
# Tariff
# =============================================================================

@pytest.mark.asyncio
async def test_get_tariff():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "electricitySupplyPoints": [{
                            "agreements": [{
                                "validFrom": "2024-01-01",
                                "validTo": None,
                                "product": {
                                    "code": "SIMPLE-2024",
                                    "displayName": "シンプルオクトパス",
                                    "standingChargePricePerDay": 28.8,
                                    "standingChargeUnitType": "JPY",
                                    "fuelCostAdjustment": {"pricePerUnitIncTax": 3.5},
                                    "renewableEnergyLevy": {"pricePerUnitIncTax": 2.0},
                                    "consumptionCharges": [{
                                        "pricePerUnitIncTax": 25.0,
                                        "band": "standard",
                                        "timeOfUse": None,
                                    }],
                                },
                            }]
                        }]
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    tariff = await client.get_tariff()
    assert tariff is not None
    assert tariff.name == "シンプルオクトパス"
    assert tariff.product_code == "SIMPLE-2024"
    assert tariff.standing_charge == 28.8
    assert tariff.rates["base"] == 25.0
    assert tariff.rates["fca"] == 3.5
    assert tariff.rates["rel"] == 2.0
    assert tariff.peak_rate == 30.5  # 25 + 3.5 + 2.0


@pytest.mark.asyncio
async def test_get_tariff_no_agreements():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "electricitySupplyPoints": [{
                            "agreements": []
                        }]
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    tariff = await client.get_tariff()
    assert tariff is None


# =============================================================================
# Supply Points
# =============================================================================

@pytest.mark.asyncio
async def test_get_supply_points():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "electricitySupplyPoints": [{
                            "spin": "SPIN123",
                            "status": "ACTIVE",
                            "meters": [{"serialNumber": "M12345"}],
                            "agreements": [{
                                "id": 1,
                                "validFrom": "2024-01-01",
                                "validTo": None,
                                "product": {
                                    "code": "SIMPLE-2024",
                                    "displayName": "シンプルオクトパス",
                                },
                            }],
                        }]
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    sps = await client.get_supply_points()
    assert len(sps) == 1
    assert sps[0].spin == "SPIN123"
    assert sps[0].meter_serial == "M12345"
    assert sps[0].agreements[0]["product_code"] == "SIMPLE-2024"


# =============================================================================
# Postal Areas (public, no auth)
# =============================================================================

@pytest.mark.asyncio
async def test_get_postal_areas():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.return_value = _mock_response({
        "data": {
            "postalAreas": [
                {
                    "postcode": "150-0002",
                    "prefecture": "東京都",
                    "city": "渋谷区",
                    "area": "渋谷",
                }
            ]
        }
    })
    client._http = mock_http

    areas = await client.get_postal_areas("150-0002")
    assert len(areas) == 1
    assert areas[0].prefecture == "東京都"
    assert areas[0].city == "渋谷区"


# =============================================================================
# GraphQL error handling
# =============================================================================

@pytest.mark.asyncio
async def test_graphql_api_error():
    client = _make_client()
    client._token = "cached-token"
    client._token_expires = datetime(2099, 1, 1)
    mock_http = AsyncMock()
    mock_http.post.return_value = _mock_response({
        "errors": [{"message": "Something went wrong"}]
    })
    client._http = mock_http

    with pytest.raises(APIError, match="Something went wrong"):
        await client._graphql("query { viewer { name } }")


@pytest.mark.asyncio
async def test_graphql_partial_error_returns_data():
    client = _make_client()
    client._token = "cached-token"
    client._token_expires = datetime(2099, 1, 1)
    mock_http = AsyncMock()
    mock_http.post.return_value = _mock_response({
        "errors": [{"message": "Partial failure"}],
        "data": {"viewer": {"name": "Test"}},
    })
    client._http = mock_http

    result = await client._graphql("query { viewer { name } }")
    assert result["viewer"]["name"] == "Test"


# =============================================================================
# Daily usage aggregation
# =============================================================================

@pytest.mark.asyncio
async def test_get_daily_usage():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.post.side_effect = [
        AUTH_RESPONSE,
        _mock_response({
            "data": {
                "account": {
                    "properties": [{
                        "electricitySupplyPoints": [{
                            "halfHourlyReadings": [
                                {
                                    "startAt": "2024-01-01T00:00:00+09:00",
                                    "endAt": "2024-01-01T00:30:00+09:00",
                                    "value": "0.5",
                                    "costEstimate": None,
                                    "consumptionStep": None,
                                    "consumptionRateBand": None,
                                },
                                {
                                    "startAt": "2024-01-01T12:00:00+09:00",
                                    "endAt": "2024-01-01T12:30:00+09:00",
                                    "value": "1.5",
                                    "costEstimate": None,
                                    "consumptionStep": None,
                                    "consumptionRateBand": None,
                                },
                            ]
                        }]
                    }]
                }
            }
        }),
    ]
    client._http = mock_http

    daily = await client.get_daily_usage(days=1)
    assert "2024-01-01" in daily
    assert daily["2024-01-01"] == 2.0
