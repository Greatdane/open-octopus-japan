#!/usr/bin/env python3
"""JSON server for SwiftUI menu bar app (Japan).

Communicates with Swift app via stdin/stdout JSON.
"""

import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from .client import OctopusClient
from .models import Tariff

# Optional agent import
try:
    from .agent import OctopusAgent
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False


def _load_env():
    """Load ~/.octopus.env into environment variables."""
    env_file = os.path.expanduser("~/.octopus.env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key.startswith("export "):
                        key = key[len("export "):].strip()
                    value = value.strip().strip("'").strip('"')
                    os.environ[key] = value


class MenuBarServer:
    """JSON server for menu bar app communication."""

    def __init__(self):
        _load_env()

        self.email = os.environ.get("OCTOPUS_EMAIL", "")
        self.password = os.environ.get("OCTOPUS_PASSWORD", "")
        self.account = os.environ.get("OCTOPUS_ACCOUNT")

        if not self.email or not self.password:
            self._output_error("Missing OCTOPUS_EMAIL or OCTOPUS_PASSWORD in ~/.octopus.env")
            sys.exit(1)

        self.client = OctopusClient(
            email=self.email,
            password=self.password,
            account=self.account,
        )

    def _output(self, data: dict):
        """Output JSON to stdout (for Swift to read)."""
        print(json.dumps(data), flush=True)

    def _output_error(self, message: str):
        """Output error JSON."""
        self._output({"error": message})

    async def fetch_data(self) -> dict[str, Any]:
        """Fetch all data from Octopus Energy Japan API."""
        result = self._base_response()

        try:
            async with self.client:
                # Account balance
                account = await self.client.get_account()
                result["balance"] = abs(account.balance)
                result["balance_is_credit"] = account.balance > 0

                # Tariff and current rate
                tariff = await self.client.get_tariff()
                if tariff:
                    result["tariff_name"] = tariff.name
                    result["standing_charge"] = tariff.standing_charge
                    result["peak_rate"] = tariff.peak_rate
                    result["fca"] = tariff.rates.get("fca", 0.0)
                    result["rel"] = tariff.rates.get("rel", 0.0)

                    # Send tiered rates to Swift (keys like "0-15kWh")
                    tier_rates = {k: v for k, v in tariff.rates.items() if "kWh" in k}
                    result["tier_rates"] = tier_rates

                    rate_info = self.client.get_current_rate(tariff)
                    result["rate"] = rate_info.rate

                # Consumption data
                try:
                    consumption = await self.client.get_consumption(periods=96)

                    daily = defaultdict(float)
                    hourly_by_day = defaultdict(lambda: defaultdict(float))
                    half_hourly_by_day = defaultdict(lambda: defaultdict(float))

                    for c in consumption:
                        day = c.start.strftime("%Y-%m-%d")
                        hour = c.start.hour
                        minute = c.start.minute
                        slot = hour * 2 + (1 if minute >= 30 else 0)
                        daily[day] += c.kwh
                        hourly_by_day[day][hour] += c.kwh
                        half_hourly_by_day[day][slot] = c.kwh

                    sorted_days = sorted(daily.keys(), reverse=True)
                    today = datetime.now().strftime("%Y-%m-%d")
                    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

                    latest_day = sorted_days[0] if sorted_days else None
                    prev_day = sorted_days[1] if len(sorted_days) > 1 else None

                    display_today = latest_day if latest_day else today
                    display_yesterday = prev_day if prev_day else yesterday

                    result["data_date_latest"] = display_today
                    result["data_date_previous"] = display_yesterday

                    if display_today in daily:
                        result["today_kwh"] = daily[display_today]
                        result["today_cost"] = self._calculate_cost(
                            daily[display_today], tariff
                        )

                    if display_yesterday in daily:
                        result["yesterday_kwh"] = daily[display_yesterday]
                        result["yesterday_cost"] = self._calculate_cost(
                            daily[display_yesterday], tariff
                        )

                    # Build hourly usage
                    hourly = []
                    if display_yesterday in hourly_by_day:
                        for h in range(24):
                            hourly.append(hourly_by_day[display_yesterday].get(h, 0))
                    if display_today in hourly_by_day:
                        for h in range(24):
                            hourly.append(hourly_by_day[display_today].get(h, 0))
                    result["hourly_usage"] = hourly[-24:] if hourly else []

                    # Build half-hourly usage
                    half_hourly = []
                    if display_yesterday in half_hourly_by_day:
                        for s in range(48):
                            half_hourly.append(half_hourly_by_day[display_yesterday].get(s, 0))
                    if display_today in half_hourly_by_day:
                        for s in range(48):
                            half_hourly.append(half_hourly_by_day[display_today].get(s, 0))
                    result["half_hourly_usage"] = half_hourly[-48:] if half_hourly else []

                    # Monthly projection
                    if result["yesterday_cost"] > 0:
                        result["monthly_projection"] = round(result["yesterday_cost"] * 30, 2)
                    elif result["today_cost"] > 0:
                        result["monthly_projection"] = round(result["today_cost"] * 30, 2)

                except Exception:
                    pass  # No consumption data yet

        except Exception as e:
            result["error"] = str(e)

        return result

    def _calculate_cost(self, total_kwh: float, tariff: Optional[Tariff]) -> float:
        """Calculate cost for a day's usage using tiered pricing (yen)."""
        if not tariff:
            return 0.0

        # Parse tier rates (keys like "0-15kWh", "15-120kWh", "300-∞kWh")
        tiers: list[tuple[float, float, float]] = []  # (start, end, rate)
        for key, rate in tariff.rates.items():
            if "kWh" not in key:
                continue
            parts = key.replace("kWh", "").split("-")
            if len(parts) == 2:
                start = float(parts[0])
                end = float("inf") if parts[1] in ("∞", "inf", "") else float(parts[1])
                tiers.append((start, end, rate))

        if tiers:
            # Sort tiers by start kWh
            tiers.sort(key=lambda t: t[0])
            cost = 0.0
            remaining = total_kwh
            for start, end, rate in tiers:
                tier_kwh = min(remaining, end - start)
                if tier_kwh <= 0:
                    break
                cost += tier_kwh * rate
                remaining -= tier_kwh
        else:
            # Flat rate fallback
            rate = tariff.peak_rate or tariff.rates.get("standard", 0)
            cost = total_kwh * rate

        # Add FCA + REL per kWh
        fca = tariff.rates.get("fca", 0)
        rel = tariff.rates.get("rel", 0)
        cost += total_kwh * (fca + rel)

        # Add standing charge
        cost += tariff.standing_charge

        return round(cost, 2)

    def _base_response(self) -> dict[str, Any]:
        """Return base response structure with all required fields."""
        return {
            "timestamp": datetime.now().isoformat(),
            "rate": None,
            "balance": 0.0,
            "balance_is_credit": False,
            "yesterday_kwh": 0.0,
            "yesterday_cost": 0.0,
            "today_kwh": 0.0,
            "today_cost": 0.0,
            "hourly_usage": [],
            "half_hourly_usage": [],
            "tariff_name": None,
            "standing_charge": 0.0,
            "peak_rate": None,
            "fca": 0.0,
            "rel": 0.0,
            "tier_rates": {},
            "monthly_projection": 0.0,
        }

    async def handle_ask(self, question: str) -> dict[str, Any]:
        """Handle AI question."""
        result = self._base_response()

        if not HAS_AGENT:
            result["error"] = "Agent not installed. Run: pip install 'open-octopus[agent]'"
            return result

        try:
            agent = OctopusAgent(
                email=self.email,
                password=self.password,
                account=self.account,
            )
            response = await agent.ask(question)
            result["response"] = response
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    async def run(self):
        """Main run loop - read commands from stdin, output to stdout."""
        data = await self.fetch_data()
        self._output(data)

        loop = asyncio.get_event_loop()

        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)

                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    self._output_error(f"Invalid JSON: {line}")
                    continue

                command = cmd.get("command", "")

                if command == "refresh":
                    data = await self.fetch_data()
                    self._output(data)
                elif command == "ask":
                    question = cmd.get("question", "")
                    if question:
                        result = await self.handle_ask(question)
                        self._output(result)
                    else:
                        self._output_error("Missing question")
                elif command == "quit":
                    break
                else:
                    self._output_error(f"Unknown command: {command}")

            except Exception as e:
                self._output_error(str(e))


def main():
    """Entry point for menu bar server."""
    server = MenuBarServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
