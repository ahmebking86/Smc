"""Conversation state constants for PTB ConversationHandler."""

(
    # ── API setup ────────────────────────────────────────────────────────────
    WAIT_API_KEY,
    WAIT_API_SECRET,

    # ── Create rebalance portfolio ───────────────────────────────────────────
    WAIT_SYMBOLS,           # enter up to 20 coins
    WAIT_TOTAL_AMOUNT,      # total USDT to invest
    WAIT_ALLOCATIONS,       # percentages for each coin (must sum 100%)
    WAIT_REBALANCE_MODE,    # time | percent
    WAIT_TIME_INTERVAL,     # hours between rebalances
    WAIT_THRESHOLD_PCT,     # deviation % that triggers rebalance
    WAIT_CONFIRM,           # final confirmation

    # ── Replace asset ────────────────────────────────────────────────────────
    WAIT_REPLACE_NEW_SYMBOL,
    WAIT_REPLACE_CONFIRM,

    # ── New features ─────────────────────────────────────────────────────────
    WAIT_DELETE_CONFIRM,
    WAIT_ADD_FUNDS_AMOUNT,
    WAIT_ADD_FUNDS_CONFIRM,
    WAIT_REDUCE_FUNDS_AMOUNT,
    WAIT_REDUCE_FUNDS_CONFIRM,

    # ── Add new asset ────────────────────────────────────────────────────────
    WAIT_ADD_ASSET_SYMBOL,
    WAIT_ADD_ASSET_AMOUNT,
    WAIT_ADD_ASSET_CONFIRM,

) = range(19)
