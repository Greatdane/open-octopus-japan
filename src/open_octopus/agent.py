#!/usr/bin/env python3
"""Claude Agent SDK integration for natural language energy queries (Japan).

Ask questions about your Octopus Energy Japan account in plain English:
- "What's my current energy usage?"
- "How much did I use yesterday?"
- "What's my electricity rate?"

Usage:
    octopus-ask "What's my balance?"

Or as a library:
    from open_octopus.agent import OctopusAgent

    agent = OctopusAgent()
    response = await agent.ask("What's my balance?")
"""

import asyncio
import json
import os
from typing import Optional

from anthropic import Anthropic

from .client import OctopusClient

# Tool definitions for Claude
OCTOPUS_TOOLS = [
    {
        "name": "get_account_info",
        "description": "Get Octopus Energy Japan account information including balance, billing name, and status",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_current_rate",
        "description": "Get the current electricity rate in yen per kWh",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_daily_usage",
        "description": "Get electricity usage for recent days in kWh",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to get usage for (default 7)",
                    "default": 7
                }
            },
            "required": []
        }
    },
    {
        "name": "get_tariff_info",
        "description": "Get electricity tariff details including name, standing charge, and unit rates",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
]


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


class OctopusAgent:
    """Claude-powered agent for natural language energy queries (Japan)."""

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        account: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514"
    ):
        """
        Initialize the Octopus Agent.

        Args:
            email: Octopus account email (or OCTOPUS_EMAIL env var)
            password: Octopus account password (or OCTOPUS_PASSWORD env var)
            account: Account number (or OCTOPUS_ACCOUNT env var)
            anthropic_api_key: Anthropic API key (or ANTHROPIC_API_KEY env var)
            model: Claude model to use
        """
        _load_env()

        self.octopus = OctopusClient(
            email=email or os.environ.get("OCTOPUS_EMAIL", ""),
            password=password or os.environ.get("OCTOPUS_PASSWORD", ""),
            account=account or os.environ.get("OCTOPUS_ACCOUNT"),
        )

        self.anthropic = Anthropic(
            api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.model = model

    async def _execute_tool(self, name: str, input_data: dict) -> dict:
        """Execute a tool and return results."""
        async with self.octopus:
            if name == "get_account_info":
                account = await self.octopus.get_account()
                return {
                    "balance_yen": account.balance,
                    "balance_status": "credit" if account.balance < 0 else "owed",
                    "name": account.name,
                    "status": account.status,
                    "address": account.address
                }

            elif name == "get_current_rate":
                tariff = await self.octopus.get_tariff()
                if not tariff:
                    return {"error": "Could not fetch tariff information"}

                rate = self.octopus.get_current_rate(tariff)
                return {
                    "current_rate_yen": rate.rate,
                    "standing_charge_yen_per_day": tariff.standing_charge,
                }

            elif name == "get_daily_usage":
                days = input_data.get("days", 7)
                daily = await self.octopus.get_daily_usage(days=days)

                return {
                    "usage_by_day": {
                        date: round(kwh, 2) for date, kwh in sorted(daily.items(), reverse=True)
                    },
                    "total_kwh": round(sum(daily.values()), 2),
                    "average_kwh": round(sum(daily.values()) / len(daily), 2) if daily else 0
                }

            elif name == "get_tariff_info":
                tariff = await self.octopus.get_tariff()
                if not tariff:
                    return {"error": "Could not fetch electricity tariff information"}

                return {
                    "name": tariff.name,
                    "product_code": tariff.product_code,
                    "standing_charge_yen_per_day": tariff.standing_charge,
                    "rate_yen_per_kwh": tariff.peak_rate,
                    "rates": tariff.rates,
                }

            else:
                return {"error": f"Unknown tool: {name}"}

    async def ask(self, question: str) -> str:
        """
        Ask a natural language question about your energy data.

        Args:
            question: Plain English question about energy usage, rates, etc.

        Returns:
            Natural language response from Claude
        """
        system_prompt = """You are an expert assistant for Octopus Energy Japan customers.
You help users understand their electricity usage, billing, and tariff.

Key context:
- All prices are in Japanese Yen (¥)
- Balance shown as negative means the customer has credit
- Octopus Energy Japan provides electricity only (no gas)

When answering:
- Be concise and friendly
- Use the tools to get current data before answering
- Format currency as ¥ (e.g., ¥30.5/kWh)
- If data isn't available, explain that meter readings may take time to appear
"""

        messages = [{"role": "user", "content": question}]

        response = self.anthropic.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            tools=OCTOPUS_TOOLS,
            messages=messages
        )

        # Process tool calls iteratively
        while response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    result = await self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            messages = [
                {"role": "user", "content": question},
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results}
            ]

            response = self.anthropic.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                tools=OCTOPUS_TOOLS,
                messages=messages
            )

        # Extract final text response
        for block in response.content:
            if hasattr(block, 'text'):
                return block.text

        return "I couldn't generate a response. Please try again."


async def ask(question: str) -> str:
    """Convenience function to ask a question."""
    agent = OctopusAgent()
    return await agent.ask(question)


def main():
    """CLI entry point for octopus-ask."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: octopus-ask \"Your question about energy\"")
        print("\nExamples:")
        print('  octopus-ask "What\'s my balance?"')
        print('  octopus-ask "How much did I use yesterday?"')
        print('  octopus-ask "What\'s my electricity rate?"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    try:
        response = asyncio.run(ask(question))
        print(response)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
