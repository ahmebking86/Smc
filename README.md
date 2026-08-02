# Cluster Judge (Bot 2)

Part of **Cluster Spot Guard** system.

## Role
- Reads new Cluster Signals from shared database
- Calculates conviction score
- Decides: Enter / Exit / Ignore
- Executes on MEXC Spot (Paper or Real)
- Manages positions & risk
- Reports via Telegram

## Stack
- Python 3.11+
- ccxt (MEXC)
- Railway + PostgreSQL

## Setup
1. Copy `.env.example` → `.env`
2. Fill MEXC keys + Telegram + same DATABASE_URL as Watcher
3. Start with MODE=paper
4. Deploy on Railway
