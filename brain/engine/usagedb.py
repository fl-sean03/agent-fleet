"""Token-usage accounting: the fleet's own spend, priced from public per-Mtok rates.

Sources (both honor the CONFINEMENT WALL — confined workspace transcripts never enter this DB):
  1. transcripts — every assistant message's `usage` block across all in-scope workspaces,
     deduped by the API `message.id` (streaming writes the SAME id on several jsonl lines with the
     usage repeated per content block — id-dedup is MANDATORY, empirically confirmed 2026-07-10).
  2. engine calls — the brain's own headless `claude -p` deployed calls (agentcall logs each one to
     state/usage/engine-calls.jsonl; those runs use --no-session-persistence so they leave no
     transcript and would otherwise be invisible).

Cost is NOT stored — it is computed at query time from PRICES so a price change reprices history.
Account attribution is by TIMELINE: swap-fleet.log records when the fleet moved to each account, so a
message's timestamp maps to whichever account was active then (engine calls log their account directly).
"""
import glob
import json
import os
import re
import sqlite3

from . import STATE

USAGE_DIR = f"{STATE}/usage"
DB_PATH = f"{USAGE_DIR}/usage.db"
ENGINE_LOG = f"{USAGE_DIR}/engine-calls.jsonl"
from . import AGENTS

SWAP_LOG = f"{AGENTS}/accounts/swap-fleet.log"

# ---- public pricing, per MILLION tokens (cached 2026-07-10; single source of truth) -------------
# Input base rate per model; cache/output derived by uniform multipliers below. Change ONLY here.
INPUT_PER_MTOK = {
    "claude-fable-5": 10.0, "claude-mythos-5": 10.0,
    "claude-opus-4-8": 5.0, "claude-opus-4-7": 5.0, "claude-opus-4-6": 5.0,
    "claude-opus-4-5": 5.0, "claude-opus-4-1": 15.0, "claude-opus-4": 15.0,
    "claude-sonnet-5": 3.0, "claude-sonnet-4-6": 3.0, "claude-sonnet-4-5": 3.0,
    "claude-sonnet-4": 3.0, "claude-3-7-sonnet": 3.0,
    "claude-haiku-4-5": 1.0, "claude-3-5-haiku": 0.8,
}
OUTPUT_PER_MTOK = {
    "claude-fable-5": 50.0, "claude-mythos-5": 50.0,
    "claude-opus-4-8": 25.0, "claude-opus-4-7": 25.0, "claude-opus-4-6": 25.0,
    "claude-opus-4-5": 25.0, "claude-opus-4-1": 75.0, "claude-opus-4": 75.0,
    "claude-sonnet-5": 15.0, "claude-sonnet-4-6": 15.0, "claude-sonnet-4-5": 15.0,
    "claude-sonnet-4": 15.0, "claude-3-7-sonnet": 15.0,
    "claude-haiku-4-5": 5.0, "claude-3-5-haiku": 4.0,
}
_DATE_SUFFIX = re.compile(r"-20\d{6}$")


def norm_model(m):
    """Canonicalize an API model id: strip a trailing -YYYYMMDD date suffix (transcripts carry e.g.
    `claude-haiku-4-5-20251001` / `claude-opus-4-5-20251101`; the price table is keyed on the base
    id). Unknown/synthetic ids pass through unchanged and price at $0 (flagged, never guessed)."""
    return _DATE_SUFFIX.sub("", m) if m else m
CACHE_READ_MULT = 0.1      # cache read  = 0.10 × input rate
CACHE_WRITE_5M_MULT = 1.25  # 5-min TTL cache write = 1.25 × input rate
CACHE_WRITE_1H_MULT = 2.0   # 1-hour TTL cache write = 2.00 × input rate


def price_known(model):
    return model in INPUT_PER_MTOK


def row_cost(model, input_tokens, cache_read, cw5m, cw1h, output_tokens):
    """USD for one usage row. Unknown/synthetic model → 0.0 (no public API price)."""
    if model not in INPUT_PER_MTOK:
        return 0.0
    inp = INPUT_PER_MTOK[model]
    out = OUTPUT_PER_MTOK[model]
    micro = (input_tokens * inp
             + cache_read * inp * CACHE_READ_MULT
             + cw5m * inp * CACHE_WRITE_5M_MULT
             + cw1h * inp * CACHE_WRITE_1H_MULT
             + output_tokens * out)
    return micro / 1_000_000.0


# SQL expression mirroring row_cost, for aggregate queries (kept in lock-step with row_cost above).
def _cost_sql():
    ins = " ".join(f"WHEN '{m}' THEN {r}" for m, r in INPUT_PER_MTOK.items())
    outs = " ".join(f"WHEN '{m}' THEN {r}" for m, r in OUTPUT_PER_MTOK.items())
    inp = f"(CASE model {ins} ELSE 0 END)"
    outp = f"(CASE model {outs} ELSE 0 END)"
    return (f"((input_tokens*{inp} + cache_read_tokens*{inp}*{CACHE_READ_MULT} "
            f"+ cache_write_5m_tokens*{inp}*{CACHE_WRITE_5M_MULT} "
            f"+ cache_write_1h_tokens*{inp}*{CACHE_WRITE_1H_MULT} "
            f"+ output_tokens*{outp})/1000000.0)")


COST_SQL = _cost_sql()

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    msg_id TEXT PRIMARY KEY,
    ts TEXT,
    day TEXT,
    workspace TEXT,
    enc TEXT,
    session TEXT,
    model TEXT,
    agent TEXT,          -- 'main' | 'subagent' | 'brain-engine'
    category TEXT,       -- 'real' (owner work) | 'confined' | 'tenant-sandbox'
    account TEXT,        -- resolved from the swap timeline (or logged directly by the engine)
    input_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_5m_tokens INTEGER,
    cache_write_1h_tokens INTEGER,
    output_tokens INTEGER,
    web_search INTEGER,
    web_fetch INTEGER,
    source TEXT          -- 'transcript' | 'engine'
);
CREATE INDEX IF NOT EXISTS ix_usage_day ON usage(day);
CREATE INDEX IF NOT EXISTS ix_usage_ws ON usage(workspace);
CREATE INDEX IF NOT EXISTS ix_usage_model ON usage(model);
CREATE INDEX IF NOT EXISTS ix_usage_account ON usage(account);
CREATE TABLE IF NOT EXISTS scan_state (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER
);
"""


def connect():
    os.makedirs(USAGE_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    return db


# ------------------------------------------------------------------ account timeline
_TS = re.compile(r"\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\]")
_COMPLETE = re.compile(r"===\s*swap-fleet\s*→\s*(\S+)\s+COMPLETE\s*===")


def account_timeline(log_path=None):
    """[(iso_ts, account)] sorted ascending — each entry = the moment the fleet finished moving to
    that account. Attribution: a message at time T belongs to the last entry with ts <= T.

    log_path resolves from the module global at CALL time: a default argument would bind the path
    at import, making SWAP_LOG unpatchable — tests would then silently read the real machine log.
    """
    log_path = log_path or SWAP_LOG
    out = []
    try:
        with open(log_path, errors="ignore") as fh:
            for line in fh:
                mc = _COMPLETE.search(line)
                mt = _TS.search(line)
                if mc and mt:
                    out.append((mt.group(1), mc.group(1)))
    except OSError:
        pass
    out.sort(key=lambda x: x[0])
    return out


def _account_at(ts, timeline):
    """The account active at ISO ts, via the timeline. Before the first swap record → 'pre-log'."""
    if not ts or not timeline:
        return "unknown" if not timeline else "pre-log"
    acct = "pre-log"
    for t, a in timeline:
        if t <= ts:
            acct = a
        else:
            break
    return acct
