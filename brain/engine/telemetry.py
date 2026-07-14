"""Operational-telemetry detector — the harness-level trace source the brain otherwise lacks.

The harvest/review/failures pipelines mine CONVERSATION transcripts. But harness bugs surface in
the harness's own OPERATIONAL LOGS, not in any agent's conversation: the 2026-07-12 resume-gate
strand was visible only in session-guard.log + pane state, and the idle-account 401 only in
watch.log. This module scans those logs, matches known trouble patterns, clusters recurring hits,
and returns structured signals (verbatim evidence + recurrence + affected component) for the
meta-harness propose loop. Read-only; no side effects.

Design notes:
- PATTERNS is data-driven so a new signal is one row, not new code.
- A signal's `component` (named regex group `comp`, else the pattern default) is what maps to a
  candidate source file in the propose loop, and is part of the dedup fingerprint.
- Only PROBLEM lines match — the healthy path (e.g. "401 self-heal: X refreshed OK") is not a signal;
  the FAILURE path ("... refresh FAILED") is.
"""
import datetime
import os
import re

HOME = os.path.expanduser("~")

# Log sources. Missing files are skipped (not every box has every log).
WATCH_LOG = f"{HOME}/.agents/accounts/watch.log"
GUARD_LOG = f"{HOME}/.agents/session-guard.log"
SWAP_LOG = f"{HOME}/.agents/accounts/swap-fleet.log"

DEFAULT_WINDOW_DAYS = 7
MAX_EVIDENCE = 4          # most-recent verbatim lines kept per signal
TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")   # leading [ISO...] on a log line

# Each pattern: id, log path, compiled regex, default component, severity, human title, and the
# harness source file(s) the propose loop should read to diagnose it.
PATTERNS = [
    {"id": "rotation-no-eligible", "log": WATCH_LOG,
     "re": re.compile(r"NO ELIGIBLE (?:TARGET|rotation target)"),
     "component": "rotation", "severity": 3,
     "title": "account-watch found no eligible rotation target (failover headroom exhausted)",
     "sources": ["bin/account-watch"]},
    {"id": "rotation-401-heal-failed", "log": WATCH_LOG,
     "re": re.compile(r"401 self-heal:\s*(?P<comp>\S+)\s+refresh FAILED"),
     "component": "account", "severity": 3,
     "title": "account 401 self-heal failed — refresh token likely dead (needs /login or setup-token)",
     "sources": ["bin/account-refresh", "bin/account-watch"]},
    {"id": "guard-stuck-risk", "log": GUARD_LOG,
     "re": re.compile(r"STUCK-RISK:\s*(?P<comp>\S+)\s+at\s+(?P<pct>\d+)%"),
     "component": "session", "severity": 2,
     "title": "a workspace hit high context and is at risk of stranding on the next bounce/swap",
     "sources": ["bin/session-guard", "bin/run-claude"]},
    {"id": "swap-warning", "log": SWAP_LOG,
     "re": re.compile(r"⚠.*(NOT verified|no pre-staged credential|NO refresh token|LEFT on current)"),
     "component": "swap", "severity": 2,
     "title": "swap-fleet warning — a workspace or confined workspace did not move cleanly",
     "sources": ["bin/swap-fleet", "bin/swap-account"]},
]


def _parse_ts(line):
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _tail_lines(path, max_bytes=2_000_000):
    """Last ~max_bytes of a possibly-large log, as lines. Missing file → []."""
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as fh:
            if sz > max_bytes:
                fh.seek(sz - max_bytes)
                fh.readline()   # drop the partial first line
            return fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def scan(window_days=DEFAULT_WINDOW_DAYS, now=None):
    """Scan the operational logs and return clustered signals, worst first.

    Each signal: {signal_id, fingerprint, pattern_id, component, title, severity, count,
    first_ts, last_ts, log, sources[], evidence[]}. `fingerprint` = pattern_id:component (the dedup
    key the propose loop uses). Only lines within `window_days` of `now` (UTC) count.
    """
    now = now or datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(days=window_days)
    clusters = {}   # fingerprint -> signal dict
    for pat in PATTERNS:
        for line in _tail_lines(pat["log"]):
            m = pat["re"].search(line)
            if not m:
                continue
            ts = _parse_ts(line)
            if ts is not None and ts < cutoff:
                continue
            comp = (m.groupdict().get("comp") or pat["component"])
            fp = f"{pat['id']}:{comp}"
            sig = clusters.get(fp)
            if sig is None:
                sig = clusters[fp] = {
                    "signal_id": fp, "fingerprint": fp, "pattern_id": pat["id"],
                    "component": comp, "title": pat["title"], "severity": pat["severity"],
                    "count": 0, "first_ts": None, "last_ts": None,
                    "log": os.path.basename(pat["log"]), "sources": list(pat["sources"]),
                    "evidence": []}
            sig["count"] += 1
            iso = ts.isoformat() + "Z" if ts else None
            if iso:
                if sig["first_ts"] is None or iso < sig["first_ts"]:
                    sig["first_ts"] = iso
                if sig["last_ts"] is None or iso > sig["last_ts"]:
                    sig["last_ts"] = iso
            sig["evidence"].append(line.strip()[:300])
    # keep only the most-recent MAX_EVIDENCE evidence lines per signal
    for sig in clusters.values():
        sig["evidence"] = sig["evidence"][-MAX_EVIDENCE:]
    return sorted(clusters.values(), key=lambda s: (s["severity"], s["count"]), reverse=True)
