"""Command-line interface for Open Octopus."""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich.columns import Columns
from rich.progress import Progress, BarColumn, TextColumn
from rich import box
from collections import defaultdict

from .client import OctopusClient, OctopusError

app = typer.Typer(
    name="octopus",
    help="Open Octopus - CLI for Octopus Energy API",
    no_args_is_help=True
)
console = Console()


def get_client() -> OctopusClient:
    """Create client from environment variables."""
    api_key = os.environ.get("OCTOPUS_API_KEY")
    account = os.environ.get("OCTOPUS_ACCOUNT")
    mpan = os.environ.get("OCTOPUS_MPAN")
    meter_serial = os.environ.get("OCTOPUS_METER_SERIAL")

    if not api_key or not account:
        console.print("[red]Error:[/] OCTOPUS_API_KEY and OCTOPUS_ACCOUNT must be set")
        console.print("\nSet environment variables:")
        console.print("  export OCTOPUS_API_KEY='sk_live_xxx'")
        console.print("  export OCTOPUS_ACCOUNT='A-XXXXXXXX'")
        raise typer.Exit(1)

    return OctopusClient(api_key, account, mpan, meter_serial)


def run_async(coro):
    """Run an async function."""
    return asyncio.run(coro)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

@app.command()
def account():
    """Show account balance and info."""
    async def _run():
        async with get_client() as client:
            acc = await client.get_account()

            balance_color = "green" if acc.balance < 0 else "yellow"
            balance_text = f"£{abs(acc.balance):.2f} {'credit' if acc.balance < 0 else 'owed'}"

            console.print(Panel(
                f"[bold]{acc.name}[/]\n"
                f"Account: {acc.number}\n"
                f"Status: {acc.status}\n"
                f"Balance: [{balance_color}]{balance_text}[/]\n"
                f"Address: {acc.address}",
                title="Octopus Energy Account"
            ))

    run_async(_run())


@app.command()
def rate():
    """Show current electricity rate."""
    async def _run():
        async with get_client() as client:
            tariff = await client.get_tariff()
            if not tariff:
                console.print("[red]Could not fetch tariff info[/]")
                return

            current = client.get_current_rate(tariff)
            time_left = current.period_end - datetime.now()
            hours = int(time_left.total_seconds()) // 3600
            mins = (int(time_left.total_seconds()) % 3600) // 60

            if current.is_off_peak:
                console.print(f"[green]🌙 OFF-PEAK[/] [bold]{current.rate:.1f}p/kWh[/]")
                console.print(f"   Ends in {hours}h {mins}m (at 05:30)")
            else:
                console.print(f"[yellow]☀️ PEAK[/] [bold]{current.rate:.1f}p/kWh[/]")
                console.print(f"   Cheap rate in {hours}h {mins}m (at 23:30)")

            console.print(f"\n[dim]Tariff: {tariff.name}[/]")
            console.print(f"[dim]Standing charge: {tariff.standing_charge:.1f}p/day[/]")

    run_async(_run())


@app.command()
def dispatch():
    """Show Intelligent Octopus dispatch status."""
    async def _run():
        async with get_client() as client:
            status = await client.get_dispatch_status()

            if status.is_dispatching and status.current_dispatch:
                d = status.current_dispatch
                console.print(f"[green bold]⚡ CHARGING NOW[/]")
                console.print(f"   Until {d.end.strftime('%H:%M')}")
            elif status.next_dispatch:
                d = status.next_dispatch
                now = datetime.now().astimezone(d.start.tzinfo)
                delta = d.start - now
                hours = int(delta.total_seconds()) // 3600
                mins = (int(delta.total_seconds()) % 3600) // 60
                console.print(f"[blue]🔌 Next charge:[/] {d.start.strftime('%H:%M')} - {d.end.strftime('%H:%M')}")
                console.print(f"   In {hours}h {mins}m ({d.duration_minutes}min window)")
            else:
                console.print("[dim]🔌 No dispatches scheduled[/]")

            # Show all upcoming dispatches
            dispatches = await client.get_dispatches()
            if len(dispatches) > 1:
                console.print("\n[bold]Upcoming dispatches:[/]")
                for d in dispatches[:5]:
                    console.print(f"  • {d.start.strftime('%a %H:%M')} - {d.end.strftime('%H:%M')}")

    run_async(_run())


@app.command()
def power():
    """Show live power consumption (requires Home Mini)."""
    async def _run():
        async with get_client() as client:
            live = await client.get_live_power()

            if live:
                watts = live.demand_watts
                if watts >= 1000:
                    power_str = f"{watts/1000:.2f} kW"
                else:
                    power_str = f"{watts} W"

                console.print(f"[bold]⚡ {power_str}[/]")
                console.print(f"[dim]   Read at {live.read_at.strftime('%H:%M:%S')}[/]")

                # Estimate hourly cost
                tariff = await client.get_tariff()
                if tariff:
                    current = client.get_current_rate(tariff)
                    cost_per_hour = (watts / 1000) * current.rate
                    console.print(f"   ~{cost_per_hour:.1f}p/hour at current rate")
            else:
                console.print("[yellow]No live power data available[/]")
                console.print("[dim]This requires a Home Mini paired with your smart meter.[/]")

    run_async(_run())


@app.command()
def sessions():
    """Show upcoming Saving Sessions (free electricity)."""
    async def _run():
        async with get_client() as client:
            sessions = await client.get_saving_sessions()

            if not sessions:
                console.print("[dim]No upcoming Saving Sessions[/]")
                return

            console.print("[bold]🎁 Saving Sessions[/]\n")
            for s in sessions:
                if s.is_active:
                    console.print(f"[green bold]⚡ ACTIVE NOW[/] until {s.end.strftime('%H:%M')}")
                else:
                    console.print(f"📅 {s.start.strftime('%a %d %b %H:%M')} - {s.end.strftime('%H:%M')}")
                console.print(f"   [dim]{s.reward_per_kwh} Octopoints per kWh saved[/]")

    run_async(_run())


@app.command()
def usage(days: int = typer.Option(7, "--days", "-d", help="Number of days")):
    """Show daily electricity usage."""
    async def _run():
        async with get_client() as client:
            try:
                daily = await client.get_daily_usage(days)
            except Exception as e:
                console.print(f"[red]Error:[/] {e}")
                console.print("[dim]Note: MPAN and meter serial required for consumption data[/]")
                return

            if not daily:
                console.print("[dim]No consumption data available[/]")
                return

            table = Table(title=f"Last {days} Days Usage")
            table.add_column("Date", style="cyan")
            table.add_column("kWh", justify="right")
            table.add_column("Graph", justify="left")

            max_kwh = max(daily.values()) if daily else 1
            for date, kwh in sorted(daily.items(), reverse=True):
                bars = int((kwh / max_kwh) * 20)
                bar_str = "█" * bars
                table.add_row(date, f"{kwh:.1f}", f"[green]{bar_str}[/]")

            console.print(table)

    run_async(_run())


@app.command()
def status():
    """Show complete status overview."""
    async def _run():
        async with get_client() as client:
            console.print("[bold]🐙 Octopus Energy Status[/]\n")

            # Account
            try:
                acc = await client.get_account()
                balance_text = f"£{abs(acc.balance):.2f} {'credit' if acc.balance < 0 else 'owed'}"
                console.print(f"💰 Balance: [bold]{balance_text}[/]")
            except OctopusError as e:
                console.print(f"[red]Account error: {e}[/]")

            # Rate
            try:
                tariff = await client.get_tariff()
                if tariff:
                    current = client.get_current_rate(tariff)
                    rate_icon = "🌙" if current.is_off_peak else "☀️"
                    console.print(f"{rate_icon} Rate: [bold]{current.rate:.1f}p/kWh[/]")
            except OctopusError:
                pass

            # Live power
            try:
                live = await client.get_live_power()
                if live:
                    power_str = f"{live.demand_kw:.2f}kW" if live.demand_watts >= 1000 else f"{live.demand_watts}W"
                    console.print(f"⚡ Power: [bold]{power_str}[/]")
            except OctopusError:
                pass

            # Dispatch
            try:
                status = await client.get_dispatch_status()
                if status.is_dispatching:
                    console.print(f"🔌 [green]CHARGING[/]")
                elif status.next_dispatch:
                    console.print(f"🔌 Next: {status.next_dispatch.start.strftime('%H:%M')}")
            except OctopusError:
                pass

            # Sessions
            try:
                sessions = await client.get_saving_sessions()
                if sessions:
                    s = sessions[0]
                    if s.is_active:
                        console.print(f"🎁 [green bold]FREE POWER[/] until {s.end.strftime('%H:%M')}")
                    else:
                        console.print(f"🎁 Session: {s.start.strftime('%a %H:%M')}")
            except OctopusError:
                pass

    run_async(_run())


@app.command()
def watch(interval: int = typer.Option(30, "--interval", "-i", help="Refresh interval in seconds")):
    """Watch live power consumption (Ctrl+C to stop)."""
    async def _run():
        from rich.live import Live

        client = get_client()
        async with client:
            with Live(console=console, refresh_per_second=1) as live:
                while True:
                    try:
                        power = await client.get_live_power()
                        tariff = await client.get_tariff()

                        if power and tariff:
                            watts = power.demand_watts
                            current = client.get_current_rate(tariff)
                            cost_per_hour = (watts / 1000) * current.rate

                            if watts >= 1000:
                                power_str = f"{watts/1000:.2f} kW"
                            else:
                                power_str = f"{watts} W"

                            rate_icon = "🌙" if current.is_off_peak else "☀️"
                            text = Text()
                            text.append(f"⚡ {power_str}", style="bold")
                            text.append(f" │ {rate_icon} {current.rate:.1f}p", style="dim")
                            text.append(f" │ ~{cost_per_hour:.0f}p/hr", style="dim")
                            live.update(Panel(text, title=f"Live Power ({power.read_at.strftime('%H:%M:%S')})"))
                        else:
                            live.update(Panel("[dim]Waiting for data...[/]"))

                        await asyncio.sleep(interval)
                    except KeyboardInterrupt:
                        break

    try:
        run_async(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped[/]")


@app.command()
def tui(refresh: int = typer.Option(60, "--refresh", "-r", help="Refresh interval in seconds")):
    """Interactive terminal dashboard with live updates."""

    SPARK_BLOCKS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    def make_sparkline(values: list, width: int = 24) -> str:
        """Create a sparkline from values."""
        if not values:
            return "─" * width
        # Normalize to width
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
        # Create main grid
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)

        # Fetch all data
        acc = None
        tariff = None
        current_rate = None
        live_power = None
        dispatch_status = None
        dispatches = []
        sessions = []
        daily_usage = {}
        hourly_today = defaultdict(float)
        hourly_yesterday = defaultdict(float)

        try:
            acc = await client.get_account()
        except:
            pass

        try:
            tariff = await client.get_tariff()
            if tariff:
                current_rate = client.get_current_rate(tariff)
        except:
            pass

        try:
            live_power = await client.get_live_power()
        except:
            pass

        try:
            dispatch_status = await client.get_dispatch_status()
            dispatches = await client.get_dispatches()
        except:
            pass

        try:
            sessions = await client.get_saving_sessions()
        except:
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
        except:
            pass

        # === HEADER ===
        header_text = Text()
        header_text.append("🐙 OPEN OCTOPUS", style="bold cyan")
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
            balance_text.append(f"£{abs(acc.balance):.2f}", style=f"bold {balance_color}")
            balance_text.append(f" {'credit' if is_credit else 'owed'}", style="dim")
            balance_panel = Panel(balance_text, box=box.ROUNDED, height=5)
        else:
            balance_panel = Panel("[dim]Balance unavailable[/]", box=box.ROUNDED, height=5)

        # Rate panel
        if current_rate and tariff:
            time_left = format_time_delta(int((current_rate.period_end - datetime.now()).total_seconds()))
            rate_color = "cyan" if current_rate.is_off_peak else "yellow"
            rate_icon = "🌙" if current_rate.is_off_peak else "☀️"
            rate_text = Text()
            rate_text.append(f"{rate_icon} {'OFF-PEAK' if current_rate.is_off_peak else 'PEAK'} RATE\n", style="bold")
            rate_text.append(f"{current_rate.rate:.1f}p", style=f"bold {rate_color}")
            rate_text.append(f"/kWh  │  {time_left} left", style="dim")
            if tariff.off_peak_rate:
                rate_text.append(f"\nPeak {tariff.peak_rate:.0f}p → Off {tariff.off_peak_rate:.0f}p", style="dim")
            rate_panel = Panel(rate_text, box=box.ROUNDED, height=5)
        else:
            rate_panel = Panel("[dim]Rate unavailable[/]", box=box.ROUNDED, height=5)

        row1.add_row(balance_panel, rate_panel)
        grid.add_row(row1)

        # === ROW 2: Live Power + Dispatch ===
        row2 = Table.grid(expand=True)
        row2.add_column(ratio=1)
        row2.add_column(ratio=1)

        # Live power panel
        if live_power:
            watts = live_power.demand_watts
            power_str = f"{watts/1000:.2f}kW" if watts >= 1000 else f"{watts}W"
            power_text = Text()
            power_text.append("⚡ LIVE POWER\n", style="bold")
            power_text.append(power_str, style="bold green")
            if current_rate:
                cost_per_hr = (watts / 1000) * current_rate.rate
                power_text.append(f"  (~{cost_per_hr:.0f}p/hr)", style="dim")
            power_text.append(f"\n[dim]Updated {live_power.read_at.strftime('%H:%M:%S')}[/]")
            power_panel = Panel(power_text, box=box.ROUNDED, height=5)
        else:
            power_panel = Panel("[dim]⚡ No live power data\nRequires Home Mini[/]", box=box.ROUNDED, height=5)

        # Dispatch panel
        if dispatch_status:
            dispatch_text = Text()
            if dispatch_status.is_dispatching and dispatch_status.current_dispatch:
                d = dispatch_status.current_dispatch
                dispatch_text.append("🔌 CHARGING NOW\n", style="bold green")
                dispatch_text.append(f"Until {d.end.strftime('%H:%M')}", style="bold")
            elif dispatch_status.next_dispatch:
                d = dispatch_status.next_dispatch
                dispatch_text.append("🔌 NEXT CHARGE\n", style="bold")
                dispatch_text.append(f"{d.start.strftime('%H:%M')} - {d.end.strftime('%H:%M')}", style="bold cyan")
                now = datetime.now().astimezone(d.start.tzinfo)
                delta = d.start - now
                dispatch_text.append(f"\n[dim]In {format_time_delta(int(delta.total_seconds()))}[/]")
            else:
                dispatch_text.append("🔌 DISPATCH\n", style="bold")
                dispatch_text.append("[dim]No charges scheduled[/]")
            dispatch_panel = Panel(dispatch_text, box=box.ROUNDED, height=5)
        else:
            dispatch_panel = Panel("[dim]🔌 Dispatch unavailable[/]", box=box.ROUNDED, height=5)

        row2.add_row(power_panel, dispatch_panel)
        grid.add_row(row2)

        # === ROW 3: Today's Usage by Hour ===
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_kwh = sum(hourly_today.values())
        today_text = Text()
        today_text.append("📊 TODAY'S USAGE BY HOUR\n", style="bold")

        if hourly_today:
            # Build hourly bars
            max_hourly = max(hourly_today.values()) if hourly_today else 1
            hour_labels = ""
            hour_bars = ""
            current_hour = datetime.now().hour

            for h in range(24):
                kwh = hourly_today.get(h, 0)
                # Color: cyan for off-peak (0-5, 23), yellow for peak
                is_off_peak = h <= 5 or h == 23
                color = "cyan" if is_off_peak else "yellow"
                if kwh > 0:
                    bar_height = int((kwh / max_hourly) * 7)
                    bar_height = max(1, min(7, bar_height))
                    hour_bars += f"[{color}]{SPARK_BLOCKS[bar_height]}[/]"
                else:
                    hour_bars += "[dim]▁[/]"

            today_text.append(f"{hour_bars}\n")
            today_text.append(f"0    6    12   18   24  │  Total: {today_kwh:.1f} kWh", style="dim")
        else:
            today_text.append("[dim]No usage data yet[/]")

        grid.add_row(Panel(today_text, box=box.ROUNDED))

        # === ROW 4: 7-Day Usage Comparison ===
        if daily_usage:
            week_text = Text()
            week_text.append("📈 7-DAY USAGE\n", style="bold")

            sorted_days = sorted(daily_usage.items(), reverse=True)[:7]
            max_daily = max(daily_usage.values()) if daily_usage else 1

            for date_str, kwh in sorted_days:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    day_name = dt.strftime("%a")
                except:
                    day_name = date_str[:3]

                bar = make_bar(kwh, max_daily, 25, "green")
                week_text.append(f"{day_name} │ {bar} {kwh:5.1f} kWh\n")

            # Add weekly total and average
            total = sum(daily_usage.values())
            avg = total / len(daily_usage) if daily_usage else 0
            week_text.append(f"[dim]Weekly: {total:.1f} kWh  │  Avg: {avg:.1f} kWh/day[/]")

            grid.add_row(Panel(week_text, box=box.ROUNDED))

        # === ROW 5: Saving Sessions + Upcoming Dispatches ===
        row5 = Table.grid(expand=True)
        row5.add_column(ratio=1)
        row5.add_column(ratio=1)

        # Saving sessions panel
        if sessions:
            session_text = Text()
            session_text.append("🎁 SAVING SESSIONS\n", style="bold")
            for s in sessions[:3]:
                if s.is_active:
                    session_text.append(f"⚡ ACTIVE until {s.end.strftime('%H:%M')}\n", style="green bold")
                else:
                    session_text.append(f"📅 {s.start.strftime('%a %d %b %H:%M')} - {s.end.strftime('%H:%M')}\n")
                session_text.append(f"   [dim]{s.reward_per_kwh} Octopoints/kWh[/]\n")
            session_panel = Panel(session_text, box=box.ROUNDED)
        else:
            session_panel = Panel("[dim]🎁 No Saving Sessions[/]", box=box.ROUNDED)

        # Upcoming dispatches panel
        if dispatches:
            disp_text = Text()
            disp_text.append("📅 UPCOMING CHARGES\n", style="bold")
            for d in dispatches[:4]:
                disp_text.append(f"• {d.start.strftime('%a %H:%M')} - {d.end.strftime('%H:%M')}\n")
            disp_panel = Panel(disp_text, box=box.ROUNDED)
        else:
            disp_panel = Panel("[dim]📅 No scheduled charges[/]", box=box.ROUNDED)

        row5.add_row(session_panel, disp_panel)
        grid.add_row(row5)

        # === FOOTER ===
        footer_text = Text()
        if tariff:
            footer_text.append(f"Tariff: {tariff.name}", style="dim")
            footer_text.append(f"  │  Standing: {tariff.standing_charge:.1f}p/day", style="dim")
        footer_text.append(f"  │  [cyan]github.com/abracadabra50/open-octopus[/]", style="dim")
        grid.add_row(Panel(footer_text, box=box.ROUNDED))

        return grid

    async def _run():
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


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
