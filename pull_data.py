"""
Pulls fresh account/position/order data from Alpaca and rewrites the
docs/data/*.enc files that the dashboard (docs/index.html) reads.

The repo is public, so every file it holds is AES-256-GCM encrypted with a
key derived (PBKDF2-HMAC-SHA256) from DASHBOARD_PASSWORD before it's
written — the dashboard prompts for that password and decrypts client-side
with the Web Crypto API. This is meant to keep the raw account/trade data
private-ish on a public repo, not to be bank-grade security: someone with
real technical skill and motivation to attack a client-side decrypt could
still get in.

Run from inside aTrade-dashboard/. Reads Alpaca keys + DASHBOARD_PASSWORD
from ../.env (the aTrade project's existing, git-ignored credentials
file) — no secrets are ever written into this repo in plaintext.
"""
import os
import json
import base64
import datetime
import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))

ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "True").lower() in ("1", "true", "yes")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
BASE = "https://paper-api.alpaca.markets" if ALPACA_PAPER else "https://api.alpaca.markets"
HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

if not ALPACA_PAPER:
    raise SystemExit("Refusing: ALPACA_PAPER is not True — this dashboard only publishes paper data.")
if not DASHBOARD_PASSWORD:
    raise SystemExit("Missing DASHBOARD_PASSWORD in ../.env — required to encrypt published data.")

DATA_DIR = os.path.join(HERE, "docs", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Fixed, non-secret salt — reused across runs so old + new encrypted files
# stay decryptable with the same derived key. Salts don't need to be secret,
# only unique-ish per application; it's committed in every .enc file anyway.
PBKDF2_SALT = b"aTrade-dashboard-v1-salt"
PBKDF2_ITERATIONS = 200_000


def derive_key(password: str) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=PBKDF2_SALT, iterations=PBKDF2_ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def encrypt_text(plaintext: str, key: bytes) -> dict:
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)  # ciphertext + 16-byte tag appended
    return {
        "salt": base64.b64encode(PBKDF2_SALT).decode(),
        "iterations": PBKDF2_ITERATIONS,
        "iv": base64.b64encode(iv).decode(),
        "ct": base64.b64encode(ct).decode(),
    }


def write_encrypted(filename: str, plaintext: str, key: bytes):
    envelope = encrypt_text(plaintext, key)
    with open(os.path.join(DATA_DIR, filename), "w") as f:
        json.dump(envelope, f)


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def main():
    key = derive_key(DASHBOARD_PASSWORD)

    # --- account snapshot: one row per calendar date (UTC) ---
    account = requests.get(f"{BASE}/v2/account", headers=HEADERS, timeout=20).json()
    today = datetime.date.today().isoformat()

    # decrypt existing history (if any) to append to it
    hist_path = os.path.join(DATA_DIR, "account_history.enc")
    rows = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path) as f:
                envelope = json.load(f)
            iv = base64.b64decode(envelope["iv"]); ct = base64.b64decode(envelope["ct"])
            plaintext = AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
            rows = [json.loads(l) for l in plaintext.split("\n") if l.strip()]
        except Exception as e:
            print(f"WARNING: could not decrypt existing account_history.enc ({e}) — starting fresh.")
            rows = []
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
    write_encrypted("account_history.enc", "\n".join(json.dumps(r) for r in rows), key)

    # --- current positions snapshot ---
    positions = requests.get(f"{BASE}/v2/positions", headers=HEADERS, timeout=20).json()
    write_encrypted("positions.enc", json.dumps({"captured_at": now_iso(), "positions": positions}), key)

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

    trades_path = os.path.join(DATA_DIR, "trades.enc")
    existing = {}
    if os.path.exists(trades_path):
        try:
            with open(trades_path) as f:
                envelope = json.load(f)
            iv = base64.b64decode(envelope["iv"]); ct = base64.b64decode(envelope["ct"])
            plaintext = AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
            for l in plaintext.split("\n"):
                if l.strip():
                    o = json.loads(l)
                    existing[o["id"]] = o
        except Exception as e:
            print(f"WARNING: could not decrypt existing trades.enc ({e}) — starting fresh.")
            existing = {}
    for o in all_orders:
        existing[o["id"]] = o
    ordered = sorted(existing, key=lambda k: existing[k].get("submitted_at") or "")
    write_encrypted("trades.enc", "\n".join(json.dumps(existing[oid]) for oid in ordered), key)

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
    write_encrypted("plan.enc", json.dumps(plan), key)

    print(f"Updated (encrypted) data for {today}: equity=${float(account['equity']):,.2f}, "
          f"positions={len(positions) if isinstance(positions, list) else 0}, "
          f"orders_on_file={len(existing)}")


if __name__ == "__main__":
    main()
