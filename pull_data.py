"""
Pulls fresh account/position/order data for every ACTIVE account listed in
../accounts.json and rewrites docs/data/<slug>/*.enc plus docs/data/manifest.enc.

The repo is public, so every file it holds is AES-256-GCM encrypted with a
key derived (PBKDF2-HMAC-SHA256) from DASHBOARD_PASSWORD before it's
written — the dashboard prompts for that password and decrypts client-side
with the Web Crypto API. Meant to keep raw account/trade data private-ish
on a public repo, not to be bank-grade security.

An account is skipped (with a warning, not a crash) if its env_key/env_secret
aren't both set in ../.env, or if "active" is false in accounts.json — lets
you register a plan before its real credentials exist.

Run from inside aTrade-dashboard/. Reads Alpaca keys + DASHBOARD_PASSWORD
from ../.env — no secrets are ever written into this repo in plaintext.
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
PROJECT_ROOT = os.path.join(HERE, "..")
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    raise SystemExit("Missing DASHBOARD_PASSWORD in ../.env — required to encrypt published data.")

DATA_DIR = os.path.join(HERE, "docs", "data")
os.makedirs(DATA_DIR, exist_ok=True)

PBKDF2_SALT = b"aTrade-dashboard-v1-salt"
PBKDF2_ITERATIONS = 200_000


def derive_key(password: str) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=PBKDF2_SALT, iterations=PBKDF2_ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def encrypt_text(plaintext: str, key: bytes) -> dict:
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return {
        "salt": base64.b64encode(PBKDF2_SALT).decode(),
        "iterations": PBKDF2_ITERATIONS,
        "iv": base64.b64encode(iv).decode(),
        "ct": base64.b64encode(ct).decode(),
    }


def decrypt_existing(path: str, key: bytes):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            envelope = json.load(f)
        iv = base64.b64decode(envelope["iv"]); ct = base64.b64decode(envelope["ct"])
        return AESGCM(key).decrypt(iv, ct, None).decode("utf-8")
    except Exception as e:
        print(f"WARNING: could not decrypt existing {path} ({e}) — starting fresh.")
        return None


def write_encrypted(path: str, plaintext: str, key: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(encrypt_text(plaintext, key), f)


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def pull_account(account_cfg: dict, key: bytes):
    slug = account_cfg["slug"]
    api_key = os.getenv(account_cfg["env_key"])
    api_secret = os.getenv(account_cfg["env_secret"])
    if not api_key or not api_secret:
        print(f"SKIP {slug}: {account_cfg['env_key']}/{account_cfg['env_secret']} not set in ../.env")
        return False

    alpaca_paper = os.getenv("ALPACA_PAPER", "True").lower() in ("1", "true", "yes")
    if not alpaca_paper:
        print(f"SKIP {slug}: ALPACA_PAPER is not True — this dashboard only publishes paper data.")
        return False

    base = "https://paper-api.alpaca.markets"
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    acct_dir = os.path.join(DATA_DIR, slug)

    account = requests.get(f"{base}/v2/account", headers=headers, timeout=20).json()
    today = datetime.date.today().isoformat()

    hist_path = os.path.join(acct_dir, "account_history.enc")
    existing_text = decrypt_existing(hist_path, key)
    rows = [json.loads(l) for l in existing_text.split("\n") if l.strip()] if existing_text else []
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
    write_encrypted(hist_path, "\n".join(json.dumps(r) for r in rows), key)

    positions = requests.get(f"{base}/v2/positions", headers=headers, timeout=20).json()
    write_encrypted(os.path.join(acct_dir, "positions.enc"),
                     json.dumps({"captured_at": now_iso(), "positions": positions}), key)

    all_orders = []
    after = None
    while True:
        params = {"status": "all", "limit": 500, "direction": "asc"}
        if after:
            params["after"] = after
        batch = requests.get(f"{base}/v2/orders", headers=headers, params=params, timeout=30).json()
        if not isinstance(batch, list) or not batch:
            break
        all_orders.extend(batch)
        if len(batch) < 500:
            break
        after = batch[-1]["submitted_at"]

    trades_path = os.path.join(acct_dir, "trades.enc")
    existing_trades_text = decrypt_existing(trades_path, key)
    existing = {}
    if existing_trades_text:
        for l in existing_trades_text.split("\n"):
            if l.strip():
                o = json.loads(l)
                existing[o["id"]] = o
    for o in all_orders:
        existing[o["id"]] = o
    ordered = sorted(existing, key=lambda k: existing[k].get("submitted_at") or "")
    write_encrypted(trades_path, "\n".join(json.dumps(existing[oid]) for oid in ordered), key)

    plan = {
        "name": account_cfg["plan_name"],
        "description": account_cfg["plan_description"],
        "params": account_cfg.get("params", {}),
        "schedule": account_cfg["schedule"],
        "updated_at": now_iso(),
    }
    write_encrypted(os.path.join(acct_dir, "plan.enc"), json.dumps(plan), key)

    print(f"OK {slug}: equity=${float(account['equity']):,.2f}, "
          f"positions={len(positions) if isinstance(positions, list) else 0}, "
          f"orders_on_file={len(existing)}")
    return True


def main():
    key = derive_key(DASHBOARD_PASSWORD)

    with open(os.path.join(PROJECT_ROOT, "accounts.json")) as f:
        config = json.load(f)

    published = []
    for account_cfg in config["accounts"]:
        if not account_cfg.get("active", True):
            print(f"SKIP {account_cfg['slug']}: marked inactive in accounts.json")
            continue
        ok = pull_account(account_cfg, key)
        if ok:
            published.append({"slug": account_cfg["slug"], "name": account_cfg["name"]})

    write_encrypted(os.path.join(DATA_DIR, "manifest.enc"),
                     json.dumps({"accounts": published, "updated_at": now_iso()}), key)
    print(f"\nManifest: {len(published)} account(s) published.")


if __name__ == "__main__":
    main()
