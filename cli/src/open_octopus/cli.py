"""Command-line interface for Open Octopus Japan."""

import asyncio
import os
from collections import defaultdict
from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .client import OctopusClient, OctopusError

app = typer.Typer(
    name="octopus",
    help="Open Octopus Japan - CLI for Octopus Energy Japan API",
    no_args_is_help=True
)
console = Console()


def load_env() -> None:
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


def get_client() -> OctopusClient:
    """Create client from environment variables."""
    load_env()
    email = os.environ.get("OCTOPUS_EMAIL")
    password = os.environ.get("OCTOPUS_PASSWORD")
    account = os.environ.get("OCTOPUS_ACCOUNT")

    if email and password:
        return OctopusClient(email=email, password=password, account=account)

    console.print("[red]Error:[/] No valid credentials found")
    console.print("\n[bold]Set credentials in ~/.octopus.env:[/]")
    console.print("  OCTOPUS_EMAIL=your-email@example.com")
    console.print("  OCTOPUS_PASSWORD=your-password")
    raise typer.Exit(1)


def run_async(coro: Coroutine[Any, Any, None]) -> None:
    """Run an async function."""
    asyncio.run(coro)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

@app.command()
def account() -> None:
    """Show account balance and info."""
    async def _run() -> None:
        async with get_client() as client:
            acc = await client.get_account()

            balance_text = f"¥{abs(acc.balance):.0f} {'credit' if acc.balance < 0 else 'owed'}"
            region = "Japan"

            info = (
                f"{acc.name}\n"
                f"Account: {acc.number}\n"
                f"Region: {region}\n"
                f"Status: {acc.status}\n"
                f"Balance: {balance_text}\n"
                f"Address: {acc.address}"
            )
            console.print(Panel(info, title="Octopus Energy Account", border_style="cyan"))

    run_async(_run())


@app.command()
def usage(
    days: int = typer.Option(7, "--days", "-d", help="Number of days (ignored if --start is set)"),
    start: Optional[str] = typer.Option(None, "--start", "-s", help="Start date (YYYY-MM-DD)"),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="End date (YYYY-MM-DD, defaults to today)"),
) -> None:
    """Show daily electricity usage."""
    async def _run() -> None:
        async with get_client() as client:
            try:
                if start:
                    start_dt = datetime.strptime(start, "%Y-%m-%d")
                    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1) if end else datetime.now()
                    all_readings = await client.get_consumption(start=start_dt, end=end_dt)
                    title = f"Usage: {start} to {end or 'today'}"
                else:
                    all_readings = await client.get_consumption(periods=days * 48)
                    title = f"Last {days} Days Usage"
            except Exception as e:
                console.print(f"[red]Error:[/] {e}")
                console.print("[dim]Note: New accounts may not have meter readings available yet[/]")
                return

            # Aggregate into daily totals and costs from a single fetch
            daily: dict[str, float] = {}
            daily_cost: dict[str, float] = {}
            for c in all_readings:
                day = c.start.strftime("%Y-%m-%d")
                daily[day] = daily.get(day, 0) + c.kwh
                if c.cost_estimate is not None:
                    daily_cost[day] = daily_cost.get(day, 0) + c.cost_estimate

            if not daily:
                console.print("[dim]No consumption data available for this period.[/]")
                return

            has_costs = bool(daily_cost)

            table = Table(title=title)
            table.add_column("Date", style="cyan")
            table.add_column("kWh", justify="right")
            if has_costs:
                table.add_column("Est. Cost", justify="right")
            table.add_column("Graph", justify="left")

            max_kwh = max(daily.values()) if daily else 1
            for date, kwh in sorted(daily.items(), reverse=True):
                bars = int((kwh / max_kwh) * 20)
                bar_str = "█" * bars
                row = [date, f"{kwh:.1f}"]
                if has_costs:
                    cost = daily_cost.get(date)
                    row.append(f"¥{cost:.0f}" if cost is not None else "—")
                row.append(f"[green]{bar_str}[/]")
                table.add_row(*row)

            console.print(table)

    run_async(_run())


@app.command()
def status() -> None:
    """Show complete status overview."""
    async def _run() -> None:
        async with get_client() as client:
            console.print("[bold]🐙 Octopus Energy Japan Status[/]\n")

            # Account
            try:
                acc = await client.get_account()
                balance_text = f"¥{abs(acc.balance):.0f} {'credit' if acc.balance < 0 else 'owed'}"
                console.print(f"💰 Balance: [bold]{balance_text}[/]")
            except OctopusError as e:
                console.print(f"[red]Account error: {e}[/]")

            # Rate
            try:
                tariff = await client.get_tariff()
                if tariff:
                    current = client.get_current_rate(tariff)
                    console.print(f"☀️ Rate: [bold]¥{current.rate:.1f}/kWh[/]")
            except OctopusError:
                pass

    run_async(_run())


@app.command()
def tariff() -> None:
    """Show current tariff details with rate breakdown."""
    async def _run() -> None:
        async with get_client() as client:
            t = await client.get_tariff()
            if not t:
                console.print("[dim]No tariff information available.[/]")
                return

            table = Table(title=f"Tariff: {t.name}")
            table.add_column("Component", style="cyan")
            table.add_column("Rate", justify="right")

            table.add_row("Product Code", t.product_code)
            table.add_row("Standing Charge", f"¥{t.standing_charge:.1f}/day")

            # Show tiered rates if present (kWh ranges), otherwise show flat rate
            tier_rates = {k: v for k, v in t.rates.items() if "kWh" in k}
            if tier_rates:
                table.add_row("", "")
                table.add_row("[bold]Consumption Tiers[/]", "")
                for label, rate in tier_rates.items():
                    table.add_row(f"  {label}", f"¥{rate:.2f}/kWh")
            elif t.rates.get("base", 0) > 0:
                table.add_row("Base Unit Rate", f"¥{t.rates.get('base', 0):.2f}/kWh")

            table.add_row("", "")
            table.add_row("Fuel Cost Adjustment", f"¥{t.rates.get('fca', 0):.2f}/kWh")
            table.add_row("Renewable Energy Levy", f"¥{t.rates.get('rel', 0):.2f}/kWh")
            table.add_row("", "─" * 15)
            table.add_row("[bold]Effective Rate (highest tier + adj)[/]", f"[bold]¥{t.peak_rate or 0:.2f}/kWh[/]")

            console.print(table)

    run_async(_run())


@app.command()
def supply() -> None:
    """Show supply point and meter details."""
    async def _run() -> None:
        async with get_client() as client:
            sps = await client.get_supply_points()
            if not sps:
                console.print("[dim]No supply points found.[/]")
                return

            for sp in sps:
                info = (
                    f"SPIN: {sp.spin}\n"
                    f"Status: {sp.status}\n"
                    f"Meter: {sp.meter_serial or 'N/A'}"
                )
                console.print(Panel(info, title="Supply Point", border_style="cyan"))

                if sp.agreements:
                    table = Table(title="Agreements")
                    table.add_column("ID")
                    table.add_column("Product")
                    table.add_column("From")
                    table.add_column("To")

                    for agr in sp.agreements:
                        table.add_row(
                            str(agr.get("id", "")),
                            agr.get("product_name", ""),
                            str(agr.get("valid_from", ""))[:10],
                            str(agr.get("valid_to", "—") or "—")[:10],
                        )
                    console.print(table)

    run_async(_run())


@app.command()
def agreements() -> None:
    """Show current and past electricity agreements."""
    async def _run() -> None:
        async with get_client() as client:
            agrs = await client.get_agreements()
            if not agrs:
                console.print("[dim]No agreements found.[/]")
                return

            table = Table(title="Electricity Agreements")
            table.add_column("ID", style="dim")
            table.add_column("Product", style="cyan")
            table.add_column("Code")
            table.add_column("Valid From")
            table.add_column("Valid To")

            for a in agrs:
                table.add_row(
                    str(a.id),
                    a.product_name,
                    a.product_code,
                    a.valid_from.strftime("%Y-%m-%d") if a.valid_from else "—",
                    a.valid_to.strftime("%Y-%m-%d") if a.valid_to else "ongoing",
                )

            console.print(table)

    run_async(_run())


@app.command()
def products(
    postcode: Optional[str] = typer.Option(None, "--postcode", "-p", help="Filter by Japanese postcode"),
) -> None:
    """Browse available electricity plans."""
    async def _run() -> None:
        async with get_client() as client:
            prods = await client.get_available_products(postcode=postcode)
            if not prods:
                console.print("[dim]No products available (or endpoint not supported).[/]")
                return

            table = Table(title="Available Products")
            table.add_column("Name", style="cyan")
            table.add_column("Code", style="dim")
            table.add_column("Standing", justify="right")
            table.add_column("Rates", justify="right")

            for p in prods:
                rate_str = ", ".join(f"{k}: ¥{v:.1f}" for k, v in p.rates.items()) if p.rates else "—"
                table.add_row(
                    p.display_name,
                    p.code,
                    f"¥{p.standing_charge:.1f}/day",
                    rate_str,
                )

            console.print(table)

    run_async(_run())


@app.command()
def billing(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of transactions"),
) -> None:
    """Show recent billing transactions."""
    async def _run() -> None:
        async with get_client() as client:
            txns = await client.get_billing(limit=limit)
            if not txns:
                console.print("[dim]No billing data available (or endpoint not supported).[/]")
                return

            table = Table(title="Billing Transactions")
            table.add_column("Date", style="cyan")
            table.add_column("Type")
            table.add_column("Description")
            table.add_column("Amount", justify="right")

            for t in txns:
                amount = t.get("amount", 0)
                color = "green" if amount < 0 else "yellow"
                table.add_row(
                    str(t.get("posted_date", ""))[:10],
                    t.get("type", ""),
                    t.get("title", ""),
                    f"[{color}]¥{abs(amount):.0f}[/]",
                )

            console.print(table)

    run_async(_run())


@app.command()
def loyalty() -> None:
    """Show loyalty points balance (if available)."""
    async def _run() -> None:
        async with get_client() as client:
            points = await client.get_loyalty_points()
            if points is None:
                console.print("[dim]Loyalty program not available on this account.[/]")
                return

            console.print(f"[bold]Loyalty Points:[/] {points.balance}")

            if points.ledger_entries:
                table = Table(title="Recent Activity")
                table.add_column("Points", justify="right")
                table.add_column("Balance", justify="right")
                table.add_column("Reason")

                for entry in points.ledger_entries[:10]:
                    table.add_row(
                        str(entry.get("value", "")),
                        str(entry.get("balance", "")),
                        entry.get("reason", ""),
                    )
                console.print(table)

    run_async(_run())


@app.command()
def tui(refresh: int = typer.Option(60, "--refresh", "-r", help="Refresh interval in seconds")) -> None:
    """Interactive terminal dashboard with live updates."""

    SPARK_BLOCKS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    def make_sparkline(values: list, width: int = 24) -> str:
        """Create a sparkline from values."""
        if not values:
            return "─" * width
        if len(values) > width:
            step = len(values) / width
            values = [values[int(i * step)] for i in range(width)]
        elif len(values) < width:
            values = values + [0] * (width - len(values))

        if not any(values):
            return "▁" * width

        min_val = min(values)
        max_val = max(values)
        range_val = max_val - min_val if max_val != min_val else 1

        result = ""
        for v in values:
            idx = int((v - min_val) / range_val * 7)
            idx = max(0, min(7, idx))
            result += SPARK_BLOCKS[idx]
        return result

    def make_bar(value: float, max_val: float, width: int = 20, color: str = "green") -> str:
        """Create a progress bar."""
        if max_val == 0:
            return "░" * width
        filled = int((value / max_val) * width)
        filled = max(0, min(width, filled))
        return f"[{color}]{'█' * filled}[/]{'░' * (width - filled)}"

    def format_time_delta(seconds: int) -> str:
        """Format seconds as Xh Xm."""
        if seconds <= 0:
            return "now"
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    async def build_dashboard(client: OctopusClient) -> Table:
        """Build the complete dashboard."""
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)

        acc = None
        tariff = None
        current_rate = None
        daily_usage = {}
        hourly_today: defaultdict[int, float] = defaultdict(float)
        hourly_yesterday: defaultdict[int, float] = defaultdict(float)

        try:
            acc = await client.get_account()
        except Exception:
            pass

        try:
            tariff = await client.get_tariff()
            if tariff:
                current_rate = client.get_current_rate(tariff)
        except Exception:
            pass

        try:
            daily_usage = await client.get_daily_usage(7)
            consumption = await client.get_consumption(periods=96)

            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            for c in consumption:
                day = c.start.strftime("%Y-%m-%d")
                hour = c.start.hour
                if day == today:
                    hourly_today[hour] += c.kwh
                elif day == yesterday:
                    hourly_yesterday[hour] += c.kwh
        except Exception:
            pass

        # === HEADER ===
        header_text = Text()
        header_text.append("🐙 OPEN OCTOPUS JAPAN", style="bold cyan")
        if acc:
            header_text.append(f"  │  {acc.name}", style="dim")
        header_text.append(f"  │  {datetime.now().strftime('%H:%M:%S')}", style="dim")
        grid.add_row(Panel(header_text, box=box.ROUNDED))

        # === ROW 1: Balance + Rate ===
        row1 = Table.grid(expand=True)
        row1.add_column(ratio=1)
        row1.add_column(ratio=1)

        # Balance panel
        if acc:
            is_credit = acc.balance > 0
            balance_color = "green" if is_credit else "yellow"
            balance_text = Text()
            balance_text.append("💰 BALANCE\n", style="bold")
            balance_text.append(f"¥{abs(acc.balance):.0f}", style=f"bold {balance_color}")
            balance_text.append(f" {'credit' if is_credit else 'owed'}", style="dim")
            balance_panel = Panel(balance_text, box=box.ROUNDED, height=5)
        else:
            balance_panel = Panel("[dim]Balance unavailable[/]", box=box.ROUNDED, height=5)

        # Rate panel
        if current_rate and tariff:
            rate_text = Text()
            rate_text.append("☀️ RATE\n", style="bold")
            rate_text.append(f"¥{current_rate.rate:.1f}", style="bold yellow")
            rate_text.append("/kWh", style="dim")
            rate_text.append(f"\nStanding: ¥{tariff.standing_charge:.1f}/day", style="dim")
            rate_panel = Panel(rate_text, box=box.ROUNDED, height=5)
        else:
            rate_panel = Panel("[dim]Rate unavailable[/]", box=box.ROUNDED, height=5)

        row1.add_row(balance_panel, rate_panel)
        grid.add_row(row1)

        # === ROW 2: Today's Usage by Hour ===
        today_kwh = sum(hourly_today.values())
        today_text = Text()
        today_text.append("📊 TODAY'S USAGE BY HOUR\n", style="bold")

        if hourly_today:
            max_hourly = max(hourly_today.values()) if hourly_today else 1
            for h in range(24):
                kwh = hourly_today.get(h, 0)
                if kwh > 0:
                    bar_height = int((kwh / max_hourly) * 7)
                    bar_height = max(1, min(7, bar_height))
                    today_text.append(SPARK_BLOCKS[bar_height], style="yellow")
                else:
                    today_text.append("▁", style="dim")

            today_text.append(f"\n0    6    12   18   24  │  Total: {today_kwh:.1f} kWh", style="dim")
        else:
            today_text.append("[dim]No usage data yet[/]")

        grid.add_row(Panel(today_text, box=box.ROUNDED))

        # === ROW 3: 7-Day Usage Comparison ===
        if daily_usage:
            week_text = Text()
            week_text.append("📈 7-DAY USAGE\n", style="bold")

            sorted_days = sorted(daily_usage.items(), reverse=True)[:7]
            max_daily = max(daily_usage.values()) if daily_usage else 1

            for date_str, kwh in sorted_days:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    day_name = dt.strftime("%a")
                except (ValueError, TypeError):
                    day_name = date_str[:3]

                bar = make_bar(kwh, max_daily, 25, "green")
                week_text.append(f"{day_name} │ {bar} {kwh:5.1f} kWh\n")

            total = sum(daily_usage.values())
            avg = total / len(daily_usage) if daily_usage else 0
            week_text.append(f"[dim]Weekly: {total:.1f} kWh  │  Avg: {avg:.1f} kWh/day[/]")

            grid.add_row(Panel(week_text, box=box.ROUNDED))

        # === FOOTER ===
        footer_text = Text()
        if tariff:
            footer_text.append(f"Tariff: {tariff.name}", style="dim")
            footer_text.append(f"  │  Standing: ¥{tariff.standing_charge:.1f}/day", style="dim")
        grid.add_row(Panel(footer_text, box=box.ROUNDED))

        return grid

    async def _run() -> None:
        console.print("[dim]Loading dashboard...[/]")
        client = get_client()

        async with client:
            with Live(console=console, refresh_per_second=0.5, screen=True) as live:
                while True:
                    try:
                        dashboard = await build_dashboard(client)
                        live.update(dashboard)
                        await asyncio.sleep(refresh)
                    except KeyboardInterrupt:
                        break
                    except Exception as e:
                        console.print(f"[red]Error: {e}[/]")
                        await asyncio.sleep(5)

    try:
        run_async(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed[/]")


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
