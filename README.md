# aTrade Dashboard — Multi-Account Trading Platform

Published, auto-updating record of every account/plan registered in
[`../accounts.json`](../accounts.json): account snapshots, current
positions, full trade history, and each plan's parameters.

**Live dashboard:** https://mohsinpatel.github.io/aTrade-dashboard/ — password-protected (ask the owner).

## Architecture

One Alpaca **paper trading** account per strategy plan. Adding a new
account/plan means:

1. Add its API key/secret to `../.env` (e.g. `ALPACA_DCA_API_KEY` / `ALPACA_DCA_SECRET_KEY`).
2. Add an entry to `../accounts.json` — slug, display name, which `.env`
   vars it uses, the plan's description/params/schedule, `"active": true`.
3. `python3 pull_data.py` picks it up automatically on the next run — no
   code changes needed here. An account missing its keys, or marked
   `"active": false`, is skipped with a warning, not a crash.

## What's here

| Path | Contents |
|---|---|
| `docs/index.html` | The dashboard — single self-contained page, no external dependencies, renders one section per account listed in `docs/data/manifest.enc`. |
| `docs/data/manifest.enc` | Which accounts are currently published (slug + display name). |
| `docs/data/<slug>/account_history.enc` | One row per calendar date: equity, cash, buying power, portfolio value. |
| `docs/data/<slug>/positions.enc` | Latest snapshot of that account's open positions. |
| `docs/data/<slug>/trades.enc` | Every order Alpaca has on file for that account, deduped by order id. |
| `docs/data/<slug>/plan.enc` | That account's plan description, parameters, and schedule. |
| `pull_data.py` | Loops over `../accounts.json`, pulls fresh data per active account from Alpaca, encrypts, writes the files above. Reads keys from `../.env` — no credentials are ever committed here. |

Every file under `docs/data/` is AES-256-GCM encrypted (key derived from
`DASHBOARD_PASSWORD` in `../.env`) — the repo is public, the data isn't
readable without the password. The dashboard decrypts client-side with
the Web Crypto API; nothing is sent anywhere.

## How it stays up to date

A scheduled task runs `pull_data.py` once a day, commits any changes, and
pushes. It runs through the linked computer (GitHub pushes aren't
reachable from the cloud sandbox in this environment), so it only
publishes on days that computer is online with the Claude desktop app
running — on a day it isn't, the dashboard just shows the last successful
snapshot until the next run catches up.

## Current plans

- **GLD Grid Plan** — replicates the risk-management style found in
  historical gold-CFD trade screenshots: fixed-size unit adds against a
  losing GLD position (no per-leg stop-loss), closes the whole grid at
  once on a bounce. Negative-skew by design; see `../gold_grid_bot.py`.
- **Dollar Cost Average / Quality Dip** — daily, before close: sells any
  held position that's green for the day, buys new positions from a
  curated large-cap "quality" list that are red for the day and not
  already held. No averaging down, but also no per-position stop-loss or
  time-based exit — its only sell trigger is a green day, so a name that
  stays red can tie up a slot for a long stretch. See
  `../quality_dip_bot.py`.

Treat every plan here as an experiment being observed in paper money, not
a proven strategy to extrapolate returns from.
