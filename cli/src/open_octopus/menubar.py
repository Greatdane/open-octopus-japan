#!/usr/bin/env python3
"""Octopus Energy Japan Mac Menu Bar App.

Energy monitoring in your menu bar.

Usage:
    octopus-menubar

Or run directly:
    python -m open_octopus.menubar
"""

import os
import asyncio
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

try:
    import rumps
except ImportError:
    rumps = None

from .client import OctopusClient
from .models import Tariff

# Optional agent import
try:
    from .agent import OctopusAgent
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False


# Guard against rumps not being installed
if rumps is None:
    raise ImportError("rumps is required for the menu bar app. Install with: pip install 'open-octopus[menubar]'")


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


class OctopusMenuBar(rumps.App):
    """Octopus Energy Japan menu bar app."""

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        account: Optional[str] = None,
    ):
        super().__init__(
            "⚡ --",
            title="⚡ --",
            quit_button=None
        )

        _load_env()

        self.email = email or os.environ.get("OCTOPUS_EMAIL", "")
        self.password = password or os.environ.get("OCTOPUS_PASSWORD", "")
        self.account_number = account or os.environ.get("OCTOPUS_ACCOUNT")

        if not self.email or not self.password:
            raise ValueError(
                "Missing credentials. Set OCTOPUS_EMAIL and OCTOPUS_PASSWORD "
                "in ~/.octopus.env or pass to constructor."
            )

        self.client = OctopusClient(
            email=self.email,
            password=self.password,
            account=self.account_number,
        )

        # State
        self.tariff: Optional[Tariff] = None
        self.balance = 0.0
        self.latest_kwh = 0.0
        self.latest_cost = 0.0
        self.latest_day = ""
        self.last_refresh: Optional[datetime] = None
        self.hourly_usage: list[float] = []

        # Build menu
        self._build_menu()

        # Timers
        self.title_timer = rumps.Timer(self._update_title, 1)
        self.title_timer.start()

        self.refresh_timer = rumps.Timer(self._refresh_timer, 30)
        self.refresh_timer.start()

        # Initial data fetch
        self._refresh_async()

    def _build_menu(self):
        """Build the menu structure."""
        self.rate_item = rumps.MenuItem("Loading...")
        self.balance_item = rumps.MenuItem("Loading...")
        self.usage_item = rumps.MenuItem("Loading...")
        self.hourly_sparkline_item = rumps.MenuItem("")
        self.status_item = rumps.MenuItem("")

        self.ask_item = rumps.MenuItem("🤖 Ask AI...", callback=self._ask_ai_clicked)
        if not HAS_AGENT:
            self.ask_item.title = "🤖 Ask AI (install agent extra)"

        self.menu = [
            self.rate_item,
            None,
            self.balance_item,
            self.usage_item,
            self.hourly_sparkline_item,
            None,
            self.ask_item,
            None,
            self.status_item,
            rumps.MenuItem("Refresh", callback=self._refresh_clicked),
            rumps.MenuItem("Open Dashboard", callback=self._open_dashboard),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self.hourly_sparkline_item.hidden = True

    @staticmethod
    def _make_sparkline(values: list[float], width: int = 24) -> str:
        """Generate ASCII sparkline from values."""
        if not values:
            return ""

        chars = "▁▂▃▄▅▆▇█"

        min_val = min(values)
        max_val = max(values)
        range_val = max_val - min_val if max_val > min_val else 1

        if len(values) > width:
            values = values[-width:]

        sparkline = ""
        for v in values:
            normalized = (v - min_val) / range_val
            index = min(int(normalized * (len(chars) - 1)), len(chars) - 1)
            sparkline += chars[index]

        return sparkline

    def _update_title(self, _):
        """Update menu bar title every second."""
        parts = []

        if self.tariff:
            now = datetime.now()
            rate_info = self.client.get_current_rate(self.tariff)
            rate = rate_info.rate

            self.rate_item.title = f"☀️ ¥{rate:.1f}/kWh"
            parts.append(f"☀️¥{rate:.0f}")

        # Build title
        if parts:
            self.title = " │ ".join(parts)
        else:
            self.title = "⚡ Loading..."

        # Update status
        if self.last_refresh:
            ago = int((datetime.now() - self.last_refresh).total_seconds())
            self.status_item.title = f"📡 Updated {ago}s ago"

    def _refresh_timer(self, _):
        """Timer callback for API refresh."""
        self._refresh_async()

    def _refresh_clicked(self, _):
        """Manual refresh button clicked."""
        self._refresh_async()

    def _open_dashboard(self, _):
        """Open Octopus dashboard in browser."""
        import subprocess
        subprocess.run(["open", "https://octopusenergy.co.jp/dashboard/"])

    def _ask_ai_clicked(self, _):
        """Ask AI button clicked - show input dialog."""
        if not HAS_AGENT:
            rumps.alert(
                title="Agent Not Installed",
                message="Install the agent extra to use AI chat:\n\npip install 'open-octopus[agent]'",
                ok="OK"
            )
            return

        window = rumps.Window(
            message="Ask a question about your energy:",
            title="🤖 Ask Octopus AI",
            default_text="",
            ok="Ask",
            cancel="Cancel",
            dimensions=(400, 60)
        )
        response = window.run()

        if response.clicked and response.text.strip():
            question = response.text.strip()
            rumps.notification(
                title="🤖 Thinking...",
                subtitle="",
                message=question[:50] + "..." if len(question) > 50 else question
            )
            thread = threading.Thread(target=self._run_ask_ai, args=(question,))
            thread.daemon = True
            thread.start()

    def _run_ask_ai(self, question: str):
        """Run AI query in background thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agent = OctopusAgent(
                email=self.email,
                password=self.password,
                account=self.account_number,
            )
            response = loop.run_until_complete(agent.ask(question))
            rumps.alert(
                title="🤖 Octopus AI",
                message=response,
                ok="OK"
            )
        except Exception as e:
            rumps.alert(
                title="Error",
                message=f"Failed to get AI response:\n{str(e)}",
                ok="OK"
            )
        finally:
            loop.close()

    def _refresh_async(self):
        """Refresh data in background thread."""
        thread = threading.Thread(target=self._run_refresh)
        thread.daemon = True
        thread.start()

    def _run_refresh(self):
        """Run the async refresh in a new event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._refresh())
        except Exception as e:
            print(f"Refresh error: {e}")
        finally:
            loop.close()

    async def _refresh(self):
        """Fetch data from Octopus Energy Japan API."""
        try:
            async with self.client:
                # Account balance
                account = await self.client.get_account()
                self.balance = account.balance

                # Tariff and rates
                self.tariff = await self.client.get_tariff()

                # Consumption data
                try:
                    consumption = await self.client.get_consumption(periods=96)
                    daily = defaultdict(float)
                    hourly_by_day = defaultdict(lambda: defaultdict(float))

                    for c in consumption:
                        day = c.start.strftime("%Y-%m-%d")
                        hour = c.start.hour
                        daily[day] += c.kwh
                        hourly_by_day[day][hour] += c.kwh

                    sorted_days = sorted(daily.keys(), reverse=True)
                    self.latest_day = sorted_days[0] if sorted_days else ""
                    self.latest_kwh = daily.get(self.latest_day, 0)

                    # Store last 24 hours of hourly usage for sparkline
                    self.hourly_usage = []
                    if len(sorted_days) >= 2:
                        today = sorted_days[0]
                        yesterday = sorted_days[1]
                        current_hour = datetime.now().hour

                        for h in range(current_hour, 24):
                            self.hourly_usage.append(hourly_by_day[yesterday].get(h, 0))
                        for h in range(0, current_hour + 1):
                            self.hourly_usage.append(hourly_by_day[today].get(h, 0))

                    # Calculate cost in yen
                    if self.tariff and self.latest_day:
                        rate = self.tariff.peak_rate or self.tariff.rates.get("standard", 0)
                        self.latest_cost = self.latest_kwh * rate + self.tariff.standing_charge
                    else:
                        self.latest_cost = 0.0

                except Exception:
                    pass  # No consumption data yet

            self.last_refresh = datetime.now()
            self._update_menu()

        except Exception as e:
            print(f"API error: {e}")

    def _update_menu(self):
        """Update menu items after refresh."""
        # Balance
        if self.balance < 0:
            self.balance_item.title = f"💰 ¥{abs(self.balance):.0f} credit"
        else:
            self.balance_item.title = f"💰 ¥{self.balance:.0f} owed"

        # Latest day usage
        if self.latest_day:
            date = datetime.strptime(self.latest_day, "%Y-%m-%d")
            today = datetime.now().date()
            if date.date() == today:
                label = "Today"
            elif date.date() == today - timedelta(days=1):
                label = "Yesterday"
            else:
                label = date.strftime("%a %d")
            self.usage_item.title = f"📈 {label}: {self.latest_kwh:.1f} kWh │ ¥{self.latest_cost:.0f}"

        # 24-hour usage sparkline
        if self.hourly_usage and len(self.hourly_usage) >= 3:
            sparkline = self._make_sparkline(self.hourly_usage, width=24)
            total_24h = sum(self.hourly_usage)
            self.hourly_sparkline_item.title = f"   {sparkline} ({total_24h:.1f} kWh/24h)"
            self.hourly_sparkline_item.hidden = False
        else:
            self.hourly_sparkline_item.hidden = True


def main():
    """Run the Octopus Energy Japan menu bar app."""
    if rumps is None:
        print("Error: rumps is required for the menu bar app.")
        print("Install with: pip install 'open-octopus[menubar]'")
        return 1

    try:
        app = OctopusMenuBar()
        app.run()
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    main()
