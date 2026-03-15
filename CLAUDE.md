# Open Octopus Japan

## Overview
Python client and CLI for the **Octopus Energy Japan** GraphQL API (`api.oejp-kraken.energy`).
Forked from the UK version ([abracadabra50/open-octopus](https://github.com/abracadabra50/open-octopus)) and adapted for Japan-only use.

## Quick Start
```bash
# Install in dev mode
pip install -e ".[all,dev]"

# Set credentials
cp .env.example ~/.octopus.env
# Edit with your OCTOPUS_EMAIL and OCTOPUS_PASSWORD

# Run CLI
octopus account
octopus usage --days 7
octopus tariff
octopus status
octopus tui
```

## Project Structure
```
src/open_octopus/
├── __init__.py          # Package exports (10 models, 4 exceptions, client)
├── models.py            # Dataclasses: Account, Consumption, Tariff, Rate,
│                        #   SupplyPoint, PostalArea, Agreement, Product,
│                        #   LoyaltyPoints, PlannedDispatch
├── client.py            # OctopusClient - async GraphQL client (core)
├── cli.py               # Typer CLI commands (account, usage, tariff, supply,
│                        #   agreements, products, billing, loyalty, status, tui)
├── agent.py             # Claude AI agent with tool use (octopus-ask)
├── menubar.py           # Python rumps macOS menu bar app (future work)
└── menubar_server.py    # JSON stdin/stdout bridge for Swift menu bar app (future work)
tests/
├── test_models.py       # Unit tests for all data models
└── test_client.py       # Client tests with mocked GraphQL responses
docs/
└── japan-api-reference.md  # Complete Japan GraphQL API reference
OctopusMenuBar/          # Xcode project (Swift macOS menu bar app, future work)
OctopusMenuBarPackage/   # SwiftUI views for the menu bar app (future work)
```

## API Details
- **Endpoint:** `https://api.oejp-kraken.energy/v1/graphql/`
- **Auth:** Email/password → JWT token (60 min expiry), refresh token (7 days)
- **Working queries:** `obtainKrakenToken`, `viewer`, `account`, `measurements` (consumption), `halfHourlyReadings` (consumption with cost), `electricitySupplyPoints.agreements.product`, `postalAreas` (public)
- **Not available on Japan API:** `billingTransactions`, `products`, `loyaltyPointLedgers`, `plannedDispatches` (all handled gracefully)
- **Consumption:** `halfHourlyReadings` tried first (returns `costEstimate`), automatic fallback to `measurements` (cursor-paginated)
- **Product types:** `ElectricitySteppedProduct` (tiered pricing, verified), `ElectricitySingleStepProduct` (flat rate). See `docs/japan-api-reference.md` for complete API reference
- **Pagination:** Cursor-based, max 100 nodes per request (API limit)
- **Rate limits:** 200 complexity points/request, 50K points/hour

## Client Methods

### Working
- `get_account()` → Account
- `get_consumption(periods, start, end)` → list[Consumption]
- `get_daily_usage(days, start, end)` → dict[str, float]
- `get_tariff()` → Optional[Tariff]
- `get_current_rate(tariff)` → Rate
- `get_supply_points()` → list[SupplyPoint]
- `get_agreements()` → list[Agreement]
- `get_postal_areas(postcode)` → list[PostalArea] (public, no auth)

### Speculative (gracefully return empty/None if not available)
- `get_available_products(postcode)` → list[Product]
- `get_billing(limit)` → list[dict]
- `get_loyalty_points()` → Optional[LoyaltyPoints]
- `get_planned_dispatches()` → list[PlannedDispatch]
- `get_communication_preferences()` → dict

### Mutations (client-only, not in CLI)
- `initiate_product_switch(product_code)` → dict
- `initiate_amperage_change(amperage)` → dict

## Development
```bash
# Run tests (43 tests)
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/open_octopus/ --ignore-missing-imports
```

## Key Conventions
- All monetary values are in JPY (yen), not pence/pounds
- Electricity only - no gas support in Japan
- Account number auto-discovered from credentials (no MPAN/meter serial needed)
- Effective rate = base unit rate + fuel cost adjustment + renewable energy levy
- Do NOT remove menubar/Swift code - macOS app is planned future work
- Speculative endpoints use `_graphql_safe()` which catches HTTP 400/GraphQL errors
- External reference repos (oejp-api-example, tako-mcp, octopus-bot) are untested - verify API patterns before adopting
