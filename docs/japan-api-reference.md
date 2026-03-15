# Octopus Energy Japan GraphQL API Reference

> Unofficial reference based on live testing against `api.oejp-kraken.energy`.
> Last verified: 2026-03-15

## Endpoint

```
POST https://api.oejp-kraken.energy/v1/graphql/
```

## Authentication

### obtainKrakenToken (mutation)

```graphql
mutation obtainKrakenToken($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    token            # JWT, valid 60 minutes
    refreshToken     # Valid 7 days
    refreshExpiresIn # Seconds until refresh expires
  }
}
```

**Input (email/password):**
```json
{ "input": { "email": "user@example.com", "password": "xxx" } }
```

**Input (refresh):**
```json
{ "input": { "refreshToken": "..." } }
```

**Header for authenticated requests:**
```
Authorization: <token>
```

Note: Some documentation says `Authorization: JWT <token>`, but bare token works on the Japan API.

---

## Queries — Working

### viewer (account discovery)

```graphql
query {
  viewer {
    accounts {
      number
    }
  }
}
```

### account

```graphql
query($account: String!) {
  account(accountNumber: $account) {
    number
    balance          # Float, JPY (negative = credit)
    billingName
    status           # "ACTIVE", etc.
    properties {
      address
      electricitySupplyPoints {
        spin           # Supply Point Identification Number
        status         # "ON_SUPPLY", etc.
        meters {
          serialNumber
        }
        agreements {
          id
          validFrom
          validTo
          product {
            __typename   # See Product Types below
            ... on ProductInterface {
              code
              displayName
              standingChargePricePerDay  # String (JPY)
              standingChargeUnitType
              fuelCostAdjustment {
                pricePerUnitIncTax       # String (JPY/kWh)
              }
              renewableEnergyLevy {
                pricePerUnitIncTax       # String (JPY/kWh)
              }
            }
            ... on ElectricitySteppedProduct {
              consumptionCharges {
                pricePerUnitIncTax  # String (JPY/kWh)
                band                # e.g. "CONSUMPTION_STEPPED_LOW_06_01"
                stepStart           # Int (kWh)
                stepEnd             # Int or null (kWh, null = unlimited)
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
```

### measurements (consumption — cursor-paginated)

This is the **working** consumption endpoint for Japan.

```graphql
query($account: String!, $startAt: DateTime, $endAt: DateTime, $after: String) {
  account(accountNumber: $account) {
    properties {
      measurements(
        startAt: $startAt,
        endAt: $endAt,
        first: 100,           # Max 100 per request
        after: $after,
        utilityFilters: [{ electricityFilters: {} }]
      ) {
        edges {
          node {
            ... on IntervalMeasurementType {
              startAt     # ISO 8601
              endAt       # ISO 8601
              value       # String (kWh)
              unit        # "kWh"
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
```

### halfHourlyReadings (consumption — simpler, but may not work for all accounts)

Used by official Octopus Japan example repo. Returns `costEstimate` when available.
**Note:** Returns "An internal error occurred" for some accounts — use `measurements` as fallback.

```graphql
query($accountNumber: String!, $fromDatetime: DateTime, $toDatetime: DateTime) {
  account(accountNumber: $accountNumber) {
    properties {
      electricitySupplyPoints {
        halfHourlyReadings(fromDatetime: $fromDatetime, toDatetime: $toDatetime) {
          startAt
          endAt
          value              # String (kWh)
          costEstimate       # String (JPY) or null
          consumptionStep    # Int or null
          consumptionRateBand # String or null
        }
      }
    }
  }
}
```

### postalAreas (public — no auth required)

```graphql
query($postcode: String!) {
  postalAreas(postcode: $postcode) {
    postcode    # "100-0001"
    prefecture  # "京都府"
    city        # "京都市中京区"
    area        # "西ノ京..."
  }
}
```

---

## Product Types (Japan-specific)

The `product` field in agreements uses a union type. Use `__typename` to identify which type you're dealing with.

| Type | Description | Verified |
|------|-------------|----------|
| `ElectricitySteppedProduct` | Tiered/stepped pricing with kWh ranges (e.g., グリーンオクトパス) | Yes |
| `ElectricitySingleStepProduct` | Flat rate pricing | Expected (not tested) |

### ElectricitySteppedProduct

Japan's main residential tariff type. Charges vary by consumption tier.

**Example (グリーンオクトパス, Kansai region):**

| Tier | Range | Rate (inc. tax) |
|------|-------|-----------------|
| Step 1 | 0–15 kWh | ¥0.00/kWh |
| Step 2 | 15–120 kWh | ¥20.08/kWh |
| Step 3 | 120–300 kWh | ¥22.70/kWh |
| Step 4 | 300+ kWh | ¥26.61/kWh |

Plus:
- Standing charge: ¥12.40/day
- Fuel cost adjustment (燃料費調整額): ¥2.71/kWh
- Renewable energy levy (再エネ賦課金): ¥3.98/kWh

**Effective rate = tier rate + fuel cost adjustment + renewable energy levy**

---

## Queries — Not Available on Japan API

These return HTTP 400 (Bad Request) when queried:

| Query | Notes |
|-------|-------|
| `billingTransactions` | Not available on account type |
| `products` / `productsForPostalArea` | Product browsing not supported |
| `loyaltyPointLedgers` | Loyalty program not available |
| `plannedDispatches` | Smart dispatch not available |
| `viewer.preferences` | Communication preferences not available |

---

## Mutations — Available (use with caution)

These are available but potentially destructive. Not all have been tested.

### initiateProductSwitch

```graphql
mutation($input: InitiateProductSwitchInput!) {
  initiateProductSwitch(input: $input) {
    switchDate
    errors { message }
  }
}
```

### initiateAmperageChange (Japan-specific)

```graphql
mutation($input: InitiateAmperageChangeInput!) {
  initiateAmperageChange(input: $input) {
    errors { message }
  }
}
```

---

## Rate Limits

| Limit | Value |
|-------|-------|
| Per-request complexity | 200 points max |
| Per-hour (user) | 50,000 points |
| Per-hour (organization) | 100,000 points |
| Nodes per request | 100 max (pagination) |
| Token validity | 60 minutes |
| Refresh token validity | 7 days |

All GraphQL responses return HTTP 200, even on errors — always check the `errors` field.
Exception: malformed queries return HTTP 400.

---

## Key Differences from UK API

| Aspect | UK | Japan |
|--------|-------|-------|
| Auth | API key (`sk_live_xxx`) | Email + password |
| Currency | GBP (pence) | JPY (yen) |
| Gas | Supported | Not available |
| Meter ID | MPAN + serial required | Auto-discovered from account |
| Consumption | REST + GraphQL | GraphQL only (`measurements` or `halfHourlyReadings`) |
| Product types | `HalfHourlyTariff`, `StandardTariff` | `ElectricitySteppedProduct`, `ElectricitySingleStepProduct` |
| Tariff structure | Unit rate + standing charge | Stepped tiers + FCA + REL + standing charge |
| Saving Sessions | Available | Not available |
| Smart devices | Extensive support | Limited/not available |
| Billing API | Available | Not available |

---

## Reference Implementations

| Project | Language | Notes |
|---------|----------|-------|
| [octoenergy/oejp-api-example](https://github.com/octoenergy/oejp-api-example) | Python | Official example (auth + halfHourlyReadings) |
| [arrow2nd/tako-mcp](https://github.com/arrow2nd/tako-mcp) | TypeScript | MCP server (account + consumption + postal areas) |
| [caru-ini/octopus-bot](https://github.com/caru-ini/octopus-bot) | Python | Discord bot (auth + halfHourlyReadings) |
