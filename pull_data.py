"""
Pulls fresh account/position/order data from Alpaca and updates the
docs/data/*.json(l) files that the dashboard (docs/index.html) reads.

Run from inside aTrade-dashboard/. Reads Alpaca keys from ../.env (the
aTrade project's existing, git-ignored credentials file) — no keys are
ever written into this repo.
"""
import os
import json
import datetime
import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))

ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "True").lower() in ("1", "true", "yes")
BASE = "https://paper-api.alpaca.markets" if ALPACA_PAPER else "https://api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

if not ALPACA_PAPER:
    raise SystemExit("Refusing: ALPACA_PAPER is not True — this dashboard only publishes paper data.")

DATA_DIR = os.path.join(HERE, "docs", "data")
os.makedirs(DATA_DIR, exist_ok=True)


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def main():
    # --- account snapshot: one row per calendar date (UTC), overwritten if re-run same day ---
    account = requests.get(f"{BASE}/v2/account", headers=HEADERS, timeout=20).json()
    today = datetime.date.today().isoformat()
    hist_path = os.path.join(DATA_DIR, "account_history.jsonl")
    rows = []
    if os.path.exists(hist_path):
        with open(hist_path) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if r["date"] != today]
    rows.append({
        "date": today,
        "equity": float(account["equity"]),
        "last_equity": float(account["last_equity"]),
        "cash": float(account["cash"]),
        "buying_power": float(account["buying_power"]),
        "portfolio_value": float(account["portfolio_value"]),
        "status": account.get("status"),
        "captured_at": now_iso(),
    })
    rows.sort(key=lambda r: r["date"])
    with open(hist_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # --- current positions snapshot ---
    positions = requests.get(f"{BASE}/v2/positions", headers=HEADERS, timeout=20).json()
    with open(os.path.join(DATA_DIR, "positions.json"), "w") as f:
        json.dump({"captured_at": now_iso(), "positions": positions}, f, indent=2)

    # --- full order/trade history, merged + deduped by order id ---
    all_orders = []
    after = None
    while True:
        params = {"status": "all", "limit": 500, "direction": "asc"}
        if after:
            params["after"] = after
        batch = requests.get(f"{BASE}/v2/orders", headers=HEADERS, params=params, timeout=30).json()
        if not isinstance(batch, list) or not batch:
            break
        all_orders.extend(batch)
        if len(batch) < 500:
            break
        after = batch[-1]["submitted_at"]

    trades_path = os.path.join(DATA_DIR, "trades.jsonl")
    existing = {}
    if os.path.exists(trades_path):
        with open(trades_path) as f:
            for l in f:
                if l.strip():
                    o = json.loads(l)
                    existing[o["id"]] = o
    for o in all_orders:
        existing[o["id"]] = o
    with open(trades_path, "w") as f:
        for oid in sorted(existing, key=lambda k: existing[k].get("submitted_at") or ""):
            f.write(json.dumps(existing[oid]) + "\n")

    # --- plan metadata (rewritten each run for freshness) ---
    plan = {
        "name": "GLD",
        "description": ("Gold grid strategy replicated from Trade patterns/Gld/ trade history: "
                         "fixed-size unit adds against a losing GLD position (no per-leg "
                         "stop-loss), closes the whole grid at once on a bounce."),
        "symbol": "GLD",
        "params": {
            "unit_pct_equity": 0.01,
            "grid_step_pct": 0.01,
            "take_profit_pct": 0.015,
            "max_grid_levels": 6,
            "max_total_exposure_pct": 0.25,
            "daily_loss_limit_pct": 0.02,
            "entry_lookback_hours": 6,
            "entry_move_threshold_pct": 0.004,
        },
        "schedule": "Hourly, 9:30am-3:30pm ET, weekdays (paper account)",
        "source": "gold_grid_bot.py in the aTrade project",
        "updated_at": now_iso(),
    }
    with open(os.path.join(DATA_DIR, "plan.json"), "w") as f:
        json.dump(plan, f, indent=2)

    print(f"Updated data for {today}: equity=${float(account['equity']):,.2f}, "
          f"positions={len(positions) if isinstance(positions, list) else 0}, "
          f"orders_on_file={len(existing)}")


if __name__ == "__main__":
    main()
