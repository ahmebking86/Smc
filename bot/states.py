"""Conversation state constants for PTB ConversationHandler."""

(
    WAIT_SYMBOL,
    WAIT_UPPER_PCT,       # legacy (kept for compat)
    WAIT_LOWER_PCT,       # legacy (kept for compat)
    WAIT_GRID_COUNT,      # legacy (kept for compat)
    WAIT_ENTRY_AMOUNT,
    WAIT_PROFIT_TARGET,   # legacy (kept for compat)
    WAIT_STOP_LOSS,
    WAIT_CONFIRM,
    WAIT_API_KEY,
    WAIT_API_SECRET,
    WAIT_PASSPHRASE,
    WAIT_TRAILING,        # legacy (kept for compat)
    WAIT_CLOSE_CONFIRM,
    WAIT_NETWORK_COUNT,   # legacy (kept for compat)
    WAIT_STEP_PCT,        # infinite grid: % step between levels
    WAIT_LIMIT_TYPE,      # legacy (kept for compat)
    # ── Template states ────────────────────────────────────────
    WAIT_TPL_NAME,
    WAIT_TPL_STEP,
    WAIT_TPL_LEVELS,
    WAIT_TPL_AMOUNT,
    WAIT_TPL_SL,
    WAIT_QUICK_SYMBOL,
    WAIT_LIMIT_PRICE,     # legacy (kept for compat)
    # ── Template advanced states ───────────────────────────────────
    WAIT_TPL_LIMIT_TYPE,
    WAIT_TPL_LIMIT_PCT,
    WAIT_TPL_TRAILING,
    WAIT_TPL_TRAILING_PCT,
    WAIT_EDIT_CHOICE,
    WAIT_EDIT_VALUE,
    # ── Manual grid advanced states ────────────────────────────────────
    WAIT_GRID_LIMIT_TYPE,
    WAIT_GRID_LIMIT_PCT,
    WAIT_GRID_TRAILING,
    WAIT_GRID_TRAILING_PCT,
    # ── Session edit states ────────────────────────────────────────────────
    WAIT_SESSION_EDIT_CHOICE,
    WAIT_SESSION_EDIT_VALUE,
    # ── Bulk edit (all sessions) states ────────────────────────────────────
    WAIT_BULK_SCOPE,
    WAIT_BULK_STEP,
    WAIT_BULK_LIMIT_TYPE,
    WAIT_BULK_LIMIT_PCT,
    WAIT_BULK_AMOUNT,
    WAIT_BULK_SL,
    WAIT_BULK_CONFIRM,
    # ── New simplified grid states ─────────────────────────────────────────
    WAIT_TAKE_PROFIT,     # take profit % when price rises
    WAIT_LEVELS_PER_SIDE, # number of grid levels per side (above + below price)
) = range(44)
