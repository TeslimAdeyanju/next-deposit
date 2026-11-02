"""
Regenerate the README screenshots (docs/images/page-*.svg) from DEMO data.

    python tools/render_screenshots.py

Uses only the fictional portfolio in demo_data.py, never a real account.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import demo_data  # noqa: E402
import insights  # noqa: E402
from rich.console import Console  # noqa: E402
from next_deposit import PAGES, Planner, PortfolioAnalytics, SnapshotManager, build_screen  # noqa: E402

WIDTH, HEIGHT = 160, 48
OUT = ROOT / "docs" / "images"


def main():
    client, snapshot_dir, insights.CONFIG_FILE = demo_data.demo_environment()
    pies = client.pies()
    details = client.pie_details(pies)
    pies = [{**p, "name": details[p["id"]]["settings"]["name"]} for p in pies]
    analytics = PortfolioAnalytics(client.account_summary(), client.positions(), client.instruments(), pies)
    config = insights.load_config(analytics.holdings)
    planner = Planner(float(config["monthly_deposit"]))
    planner.load(config)

    now = datetime.now().replace(second=0, microsecond=0)
    base = analytics.total_value
    session = [(now - timedelta(minutes=30 * (40 - i) / 40 * 4), base * (1 + 0.003 * ((i * 7) % 11 - 5) / 5 + 0.0004 * i)) for i in range(40)]

    OUT.mkdir(parents=True, exist_ok=True)
    for page, name in enumerate(PAGES):
        ctx = dict(
            analytics=analytics, page=page, width=WIDTH, height=HEIGHT, config=config, planner=planner,
            demo=True, next_in=18, updated_at=now, error=None,
            transactions=client.history_transactions(), orders=client.history_orders(),
            dividends=client.history_dividends(), pie_details=details, value_history=session,
            daily=SnapshotManager(snapshot_dir).daily_values(90, analytics.total_value),
            daily_rows=SnapshotManager(snapshot_dir).daily_rows(400, analytics),
            schedules=insights.schedules_by_id(client.exchanges()),
        )
        console = Console(width=WIDTH, record=True, file=open("/dev/null", "w"))
        console.print(build_screen(ctx))
        path = OUT / f"page-{page + 1}-{name.lower()}.svg"
        console.save_svg(str(path), title=f"Next Deposit · {name} (demo data)")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
