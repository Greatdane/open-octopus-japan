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
octopus status
octopus tui
```

## Project Structure
```
src/open_octopus/
├── __init__.py          # Package exports
├── models.py            # Dataclasses: Account, Consumption, Tariff, Rate, SupplyPoint, PostalArea
├── client.py            # OctopusClient - async GraphQL client (core)
├── cli.py               # Typer CLI commands (account, usage, status, tui)
├── agent.py             # Claude AI agent with tool use (octopus-ask)
├── menubar.py           # Python rumps macOS menu bar app (future work)
└── menubar_server.py    # JSON stdin/stdout bridge for Swift menu bar app (future work)
tests/
├── test_models.py       # Unit tests for data models
└── test_client.py       # Client tests with mocked GraphQL responses
OctopusMenuBar/          # Xcode project (Swift macOS menu bar app, future work)
OctopusMenuBarPackage/   # SwiftUI views for the menu bar app (future work)
```

## API Details
- **Endpoint:** `https://api.oejp-kraken.energy/v1/graphql/`
- **Auth:** Email/password → JWT token (60 min expiry), refresh token (7 days)
- **Key queries:** `obtainKrakenToken`, `viewer`, `account`, `halfHourlyReadings` (primary), `measurements` (fallback), `electricitySupplyPoints.agreements.product`, `postalAreas` (public)
- **Consumption:** `halfHourlyReadings` returns `costEstimate`, `consumptionStep`, `consumptionRateBand`; `measurements` (IntervalMeasurementType) is cursor-paginated fallback
- **Pagination:** Cursor-based, max 100 nodes per request (API limit)
- **Rate limits:** 200 complexity points/request, 50K points/hour

## Development
```bash
# Run tests
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
- External reference repos (oejp-api-example, tako-mcp, octopus-bot) are untested - verify API patterns before adopting
