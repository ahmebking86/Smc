"""Conversation state constants for PTB ConversationHandler."""

(
    # ── API setup ────────────────────────────────────────────────────────────
    WAIT_API_KEY,
    WAIT_API_SECRET,
    WAIT_PASSPHRASE,          # Bitget only

    # ── Create rebalance portfolio ───────────────────────────────────────────
    WAIT_SYMBOLS,
    WAIT_TOTAL_AMOUNT,
    WAIT_ALLOCATIONS,
    WAIT_REBALANCE_MODE,
    WAIT_TIME_INTERVAL,
    WAIT_THRESHOLD_PCT,
    WAIT_CONFIRM,

    # ── Replace asset ────────────────────────────────────────────────────────
    WAIT_REPLACE_NEW_SYMBOL,
    WAIT_REPLACE_CONFIRM,

    # ── Extra features ───────────────────────────────────────────────────────
    WAIT_ADD_FUNDS_AMOUNT,
    WAIT_REDUCE_FUNDS_AMOUNT,

) = range(14)
