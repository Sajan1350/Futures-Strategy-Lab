"""
Print the ProjectX accounts the bot can see, including each account's name,
canTrade flag, and balance. Run this before any paper/live session to
confirm which account bot_manager will pick up.

Usage:
    python scripts/list_accounts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from broker.projectx_client import ProjectXClient
from bot_manager import _is_likely_paper


def main() -> None:
    load_dotenv()
    client = ProjectXClient()
    client.login()

    accounts = client.get_accounts()
    if not accounts:
        print("No accounts returned. Check your API credentials in .env.")
        return

    print()
    print(f"{'id':<15} {'name':<32} {'canTrade':<10} {'balance':>14}  detected")
    print("-" * 90)
    for a in accounts:
        name = a.get("name") or ""
        detected = "PAPER" if _is_likely_paper(name) else "LIVE?"
        print(f"{a.get('id')!s:<15} "
              f"{name!r:<32} "
              f"{str(a.get('canTrade', False)):<10} "
              f"${a.get('balance', 0):>12,.2f}  "
              f"{detected}")

    print()
    tradeable = [a for a in accounts if a.get("canTrade", False)]
    default = (tradeable or accounts)[0]
    print(f"bot_manager will default to: id={default.get('id')}  "
          f"name={default.get('name')!r}")


if __name__ == "__main__":
    main()
