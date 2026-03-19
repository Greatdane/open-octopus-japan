#!/usr/bin/env python3
"""JSON server for SwiftUI menu bar app (Japan).

Communicates with Swift app via stdin/stdout JSON.
"""

import asyncio
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
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

                # Get agreement start date for billing cycle
                billing_day = 1
                try:
                    agreements = await self.client.get_agreements()
                    if agreements and agreements[0].valid_from:
                        billing_day = agreements[0].valid_from.day
                except Exception:
                    pass
                result["billing_cycle_day"] = billing_day

                # Consumption data
                try:
                    consumption = await self.client.get_consumption(periods=8 * 48)

                    daily = defaultdict(float)
                    daily_readings = defaultdict(int)
                    hourly_by_day = defaultdict(lambda: defaultdict(float))
                    half_hourly_by_day = defaultdict(lambda: defaultdict(float))

                    for c in consumption:
                        day = c.start.strftime("%Y-%m-%d")
                        hour = c.start.hour
                        minute = c.start.minute
                        slot = hour * 2 + (1 if minute >= 30 else 0)
                        daily[day] += c.kwh
                        daily_readings[day] += 1
                        hourly_by_day[day][hour] += c.kwh
                        half_hourly_by_day[day][slot] = c.kwh

                    # Drop partial first day (< 24 readings = incomplete)
                    earliest = min(daily.keys()) if daily else None
                    if earliest and daily_readings[earliest] < 24:
                        del daily[earliest]
                        daily_readings.pop(earliest, None)

                    sorted_days = sorted(daily.keys(), reverse=True)
                    today = datetime.now().strftime("%Y-%m-%d")
                    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

                    latest_day = sorted_days[0] if sorted_days else None
                    prev_day = sorted_days[1] if len(sorted_days) > 1 else None

                    display_today = latest_day if latest_day else today
                    display_yesterday = prev_day if prev_day else yesterday

                    result["data_date_latest"] = display_today
                    result["data_date_previous"] = display_yesterday

                    # Calculate cumulative kWh from billing cycle start
                    # so daily costs use the correct tier position
                    billing_start = self._billing_period_start(billing_day)
                    billing_start_str = billing_start.strftime("%Y-%m-%d")
                    cycle_days = sorted(
                        d for d in daily.keys() if d >= billing_start_str
                    )

                    # Build cumulative usage before each day
                    cumulative_before: dict[str, float] = {}
                    running = 0.0
                    for d in cycle_days:
                        cumulative_before[d] = running
                        running += daily[d]

                    if display_today in daily:
                        result["today_kwh"] = daily[display_today]
                        prior = cumulative_before.get(display_today, 0.0)
                        result["today_cost"] = self._calculate_cost(
                            daily[display_today], tariff, cycle_kwh_before=prior
                        )

                    if display_yesterday in daily:
                        result["yesterday_kwh"] = daily[display_yesterday]
                        prior = cumulative_before.get(display_yesterday, 0.0)
                        result["yesterday_cost"] = self._calculate_cost(
                            daily[display_yesterday], tariff, cycle_kwh_before=prior
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

                    # Calculate billing-cycle-aware stats
                    try:
                        billing_start = self._billing_period_start(billing_day)
                        billing_start_str = billing_start.strftime("%Y-%m-%d")

                        cycle_kwh = sum(
                            kwh for date, kwh in daily.items()
                            if date >= billing_start_str
                        )
                        marginal = self._marginal_rate(cycle_kwh, tariff)
                        if marginal is not None:
                            result["rate"] = marginal
                            result["billing_cycle_kwh"] = round(cycle_kwh, 1)

                        # Cycle cost so far (sum of daily costs within cycle)
                        cycle_cost = sum(
                            self._calculate_cost(
                                daily[d], tariff,
                                cycle_kwh_before=cumulative_before.get(d, 0.0)
                            )
                            for d in cycle_days
                        )
                        result["billing_cycle_cost"] = round(cycle_cost, 0)

                        # Project to full month based on days elapsed
                        days_elapsed = (datetime.now() - billing_start).days
                        if days_elapsed > 0:
                            daily_avg_cost = cycle_cost / days_elapsed
                            result["monthly_projection"] = round(daily_avg_cost * 30, 0)

                        # Days remaining in cycle
                        next_billing = billing_start.replace(
                            month=billing_start.month + 1 if billing_start.month < 12 else 1,
                            year=billing_start.year if billing_start.month < 12 else billing_start.year + 1,
                        )
                        result["billing_days_remaining"] = (next_billing - datetime.now()).days
                    except Exception:
                        pass

                    # Log daily usage to CSV history (with cycle-aware costs)
                    try:
                        self._log_daily_usage(dict(daily), tariff, cumulative_before)
                    except Exception:
                        pass  # Don't fail on logging errors

                except Exception:
                    pass  # No consumption data yet

        except Exception as e:
            result["error"] = str(e)

        return result

    def _calculate_cost(
        self, total_kwh: float, tariff: Optional[Tariff], cycle_kwh_before: float = 0.0
    ) -> float:
        """Calculate cost for a day's usage using tiered pricing (yen).

        Args:
            total_kwh: kWh used on this day
            tariff: Tariff with tier rates
            cycle_kwh_before: cumulative kWh already used in the billing cycle
                before this day. This positions the day's usage in the correct
                tier (tiers are monthly, not daily).
        """
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
            tiers.sort(key=lambda t: t[0])
            cost = 0.0
            remaining = total_kwh
            cumulative = cycle_kwh_before

            for start, end, rate in tiers:
                if cumulative >= end:
                    # Already past this tier entirely
                    continue
                # How much of this tier is available (accounting for prior usage)
                tier_start = max(start, cumulative)
                tier_available = end - tier_start
                tier_kwh = min(remaining, tier_available)
                if tier_kwh <= 0:
                    continue
                cost += tier_kwh * rate
                remaining -= tier_kwh
                cumulative += tier_kwh
                if remaining <= 0:
                    break
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

    def _calculate_tier_breakdown(
        self, total_kwh: float, tariff: Optional[Tariff], cycle_kwh_before: float = 0.0
    ) -> list[dict[str, Any]]:
        """Return per-tier kWh breakdown for a day's usage.

        Returns list of {"tier": "0-15kWh", "kwh": 5.0, "rate": 0.0, "cost": 0.0}
        """
        if not tariff:
            return []

        tiers: list[tuple[float, float, float, str]] = []
        for key, rate in tariff.rates.items():
            if "kWh" not in key:
                continue
            parts = key.replace("kWh", "").split("-")
            if len(parts) == 2:
                start = float(parts[0])
                end = float("inf") if parts[1] in ("∞", "inf", "") else float(parts[1])
                tiers.append((start, end, rate, key))

        if not tiers:
            return []

        tiers.sort(key=lambda t: t[0])
        fca = tariff.rates.get("fca", 0)
        rel = tariff.rates.get("rel", 0)
        breakdown = []
        remaining = total_kwh
        cumulative = cycle_kwh_before

        for start, end, rate, label in tiers:
            if cumulative >= end:
                continue
            tier_start = max(start, cumulative)
            tier_available = end - tier_start
            tier_kwh = min(remaining, tier_available)
            if tier_kwh <= 0:
                continue
            tier_cost = tier_kwh * (rate + fca + rel)
            breakdown.append({
                "tier": label,
                "kwh": round(tier_kwh, 2),
                "rate": round(rate + fca + rel, 2),
                "cost": round(tier_cost, 2),
            })
            remaining -= tier_kwh
            cumulative += tier_kwh
            if remaining <= 0:
                break

        return breakdown

    def _billing_period_start(self, billing_day: int) -> datetime:
        """Calculate the start of the current billing period."""
        now = datetime.now()
        if now.day >= billing_day:
            return now.replace(day=billing_day, hour=0, minute=0, second=0, microsecond=0)
        # Before billing day — period started last month
        if now.month == 1:
            return now.replace(year=now.year - 1, month=12, day=billing_day,
                               hour=0, minute=0, second=0, microsecond=0)
        return now.replace(month=now.month - 1, day=billing_day,
                           hour=0, minute=0, second=0, microsecond=0)

    def _marginal_rate(self, cycle_kwh: float, tariff: Optional[Tariff]) -> Optional[float]:
        """Get the marginal rate (current tier + FCA + REL) based on billing cycle usage."""
        if not tariff:
            return None

        tiers: list[tuple[float, float, float]] = []
        for key, rate in tariff.rates.items():
            if "kWh" not in key:
                continue
            parts = key.replace("kWh", "").split("-")
            if len(parts) == 2:
                start = float(parts[0])
                end = float("inf") if parts[1] in ("∞", "inf", "") else float(parts[1])
                tiers.append((start, end, rate))

        if not tiers:
            return None

        tiers.sort(key=lambda t: t[0])
        fca = tariff.rates.get("fca", 0)
        rel = tariff.rates.get("rel", 0)

        # Find which tier the current usage falls in
        for start, end, rate in tiers:
            if cycle_kwh < end:
                return round(rate + fca + rel, 2)

        # Past all tiers — use the last one
        return round(tiers[-1][2] + fca + rel, 2)

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
            "billing_cycle_day": 1,
            "billing_cycle_kwh": 0.0,
            "billing_cycle_cost": 0.0,
            "billing_days_remaining": 0,
            "monthly_projection": 0.0,
        }

    # -------------------------------------------------------------------------
    # Usage History (CSV log)
    # -------------------------------------------------------------------------

    HISTORY_FILE = Path.home() / ".octopus-usage.csv"
    HISTORY_HEADERS = ["date", "kwh", "cost", "tier_breakdown"]

    def _log_daily_usage(
        self,
        daily: dict[str, float],
        tariff: Optional[Tariff],
        cumulative_before: Optional[dict[str, float]] = None,
    ) -> None:
        """Write daily usage to CSV, updating existing rows if kWh changed."""
        existing: dict[str, dict[str, str]] = {}
        if self.HISTORY_FILE.exists():
            with open(self.HISTORY_FILE, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing[row["date"]] = row

        changed = False
        for date, kwh in sorted(daily.items()):
            prior = (cumulative_before or {}).get(date, 0.0)
            cost = self._calculate_cost(kwh, tariff, cycle_kwh_before=prior)
            breakdown = self._calculate_tier_breakdown(kwh, tariff, cycle_kwh_before=prior)
            new_row = {
                "date": date,
                "kwh": str(round(kwh, 2)),
                "cost": str(round(cost, 2)),
                "tier_breakdown": json.dumps(breakdown),
            }

            old = existing.get(date)
            if not old or old.get("kwh") != new_row["kwh"] or not old.get("tier_breakdown"):
                existing[date] = new_row
                changed = True

        if not changed:
            return

        with open(self.HISTORY_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.HISTORY_HEADERS)
            writer.writeheader()
            for date in sorted(existing.keys()):
                writer.writerow(existing[date])

    def _read_history(self, days: int = 30) -> list[dict[str, Any]]:
        """Read usage history from CSV, last N days."""
        if not self.HISTORY_FILE.exists():
            return []

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = []
        with open(self.HISTORY_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date", "") >= cutoff:
                    breakdown = []
                    try:
                        breakdown = json.loads(row.get("tier_breakdown", "[]"))
                    except (json.JSONDecodeError, TypeError):
                        pass
                    rows.append({
                        "date": row["date"],
                        "kwh": float(row.get("kwh", 0)),
                        "cost": float(row.get("cost", 0)),
                        "tier_breakdown": breakdown,
                    })
        return sorted(rows, key=lambda r: r["date"], reverse=True)

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
                elif command == "history":
                    days = cmd.get("days", 30)
                    history = self._read_history(days=days)
                    self._output({"history": history})
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
