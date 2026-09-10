# aTrade Dashboard — GLD Grid Plan

Published, auto-updating record of the **GLD grid plan** (see `gold_grid_bot.py`
in the [aTrade](../) project) running against an Alpaca **paper trading**
account: account snapshots, current position, full trade history, and the
plan's parameters.

**Live dashboard:** enabled via GitHub Pages on this repo (`main` branch,
`/docs` folder) — see the repo's Settings → Pages for the URL once the first
build finishes, or the About/Pages badge on the repo page.

## What's here

| Path | Contents |
|---|---|
| `docs/index.html` | The dashboard itself — a single self-contained page (no external dependencies) that reads the JSON files below. |
| `docs/data/account_history.jsonl` | One row per calendar date: equity, cash, buying power, portfolio value. |
| `docs/data/positions.json` | Latest snapshot of open positions. |
| `docs/data/trades.jsonl` | Every order Alpaca has on file for this account, deduped by order id, all fields Alpaca returns. |
| `docs/data/plan.json` | The GLD plan's strategy description, parameters, and schedule. |
| `pull_data.py` | Pulls fresh data from Alpaca and rewrites the files above. Reads Alpaca keys from `../.env` — no credentials are ever committed here. |

## How it stays up to date

A scheduled task ("aTrade Daily Dashboard Publish") runs `pull_data.py` once
a day after market close, commits any changes, and pushes. It runs through
your linked computer (GitHub pushes aren't reachable from the cloud sandbox
in this environment), so it only publishes on days your Mac is online with
the Claude desktop app running — on a day it isn't, the dashboard just shows
the last successful snapshot until the next run catches up.

## Why this plan looks the way it does

`gold_grid_bot.py` deliberately replicates the *risk-management style* found
in a set of historical gold-CFD trade screenshots, not a discovered chart
pattern: fixed small size per entry, more of the same size added against a
losing position (no per-trade stop-loss), the whole stack closed together on
a bounce. That's a negative-skew pattern — frequent small wins, occasional
large single-trade losses — being run here in paper money specifically so it
can be observed safely. Treat this dashboard as an experiment log, not a
performance track record to extrapolate from.
