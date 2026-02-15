#!/usr/bin/env python3
"""Test Japan consumption query."""
import asyncio, os, json
from datetime import datetime, timedelta

# Load env
env_file = os.path.expanduser("~/.octopus.env")
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')

from open_octopus.client import OctopusClient

async def test():
    client = OctopusClient(
        email=os.environ["OCTOPUS_EMAIL"],
        password=os.environ["OCTOPUS_PASSWORD"],
        region="japan"
    )
    async with client:
        token = await client._get_token()
        http = await client._get_http()

        end = datetime.now()
        start = end - timedelta(days=1)

        # Test raw query to see full error
        resp = await http.post(
            client._graphql_url,
            headers={"Authorization": token},
            json={
                "query": """
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
                "variables": {
                    "accountNumber": "A-6CBDDE7D",
                    "fromDatetime": start.isoformat(),
                    "toDatetime": end.isoformat()
                }
            }
        )
        print("Status:", resp.status_code)
        print("Response:", json.dumps(resp.json(), indent=2, default=str))

asyncio.run(test())
