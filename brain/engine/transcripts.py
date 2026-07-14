"""Transcript parsing, chunking, operator-turn detection, conversation windows (ARCHITECTURE §4).

Proven v1 logic, shared: parse user/assistant TEXT turns (never tool i/o), chunk for extraction,
and build review-style conversation windows where the operator's real turns are never dropped.
"""
import datetime
import json
import re

CHUNK_CHARS = 140_000       # ~35k tokens; most sessions are 1-3 chunks
CHUNK_OVERLAP = 2_000
MAX_CHUNKS_PER_SESSION = 12  # cap huge sessions (callers must LOG the cap — never silent)

# content that shows up as a "user" turn but is NOT the operator's voice (injected skills/commands/
# reminders/harness continuations) — must not be mistaken for operator steering.
INJECT_MARKERS = ("Base directory for this skill", "This session is being continued",
                  "[Request interrupted", "<command-", "<system-reminder", "<local-command",
                  "Caveat: The messages", "The user sent a new message while you were working")


def parse_transcript(path):
    """[(role, ts, text)] for user/assistant text turns. Robust to malformed lines."""
    turns = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"user"' not in line and '"assistant"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                role = o.get("type") or o.get("message", {}).get("role")
                if role not in ("user", "assistant"):
                    continue
                ts = o.get("timestamp", "")
                content = o.get("message", {}).get("content", o.get("content", ""))
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(b.get("text", "") for b in content
                                     if isinstance(b, dict) and b.get("type") == "text")
                text = text.strip()
                if text and not text.startswith("<"):   # skip pure reminder/command noise blocks
                    turns.append((role, ts, text))
    except FileNotFoundError:
        pass
    return turns


def chunks_from_turns(turns):
    """Labelled turn text windowed into <=CHUNK_CHARS pieces (with overlap)."""
    full = "\n\n".join(f"[{ts}] {role.upper()}:\n{text}" for (role, ts, text) in turns)
    if len(full) <= CHUNK_CHARS:
        return [full] if full.strip() else []
    out, i = [], 0
    while i < len(full):
        out.append(full[i:i + CHUNK_CHARS])
        i += CHUNK_CHARS - CHUNK_OVERLAP
    return out


def is_real_operator(txt):
    t = txt.strip()
    if not t or t.startswith("<"):
        return False
    return not any(mk in t[:160] for mk in INJECT_MARKERS)


def _trim(t, n):
    t = t.strip()
    return t if len(t) <= n else t[: n * 2 // 3] + "\n…[trimmed]…\n" + t[-n // 3:]


def _epoch(ts):
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def conversation_window(session_paths, since_epoch, budget=120_000, agent_cap=1400):
    """Chronological operator+agent conversation across sessions since `since_epoch`.
    ALL real operator turns kept verbatim (highest signal); agent turns trimmed, fill the budget.
    Returns (text, n_operator_turns, n_agent_turns)."""
    turns = []
    for path in session_paths:
        for role, ts, txt in parse_transcript(path):
            e = _epoch(ts)
            if e >= since_epoch:
                turns.append((e, role, ts, txt))
    turns.sort(key=lambda x: x[0])
    ops, agents = [], []
    for e, role, ts, txt in turns:
        if role == "user":
            if is_real_operator(txt):
                ops.append((e, f"[{(ts or '')[:16]}] OPERATOR: {txt.strip()}"))
        else:
            agents.append((e, f"[{(ts or '')[:16]}] AGENT: {_trim(txt, agent_cap)}"))
    keep = list(ops)
    used = sum(len(b) for _, b in keep)
    for e, b in agents:
        if used + len(b) > budget:
            break
        keep.append((e, b)); used += len(b)
    keep.sort(key=lambda x: x[0])
    return "\n\n".join(b for _, b in keep), len(ops), len(agents)


def norm(s):
    """Whitespace-normalized lowercase — the quote-verification canonical form."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()
