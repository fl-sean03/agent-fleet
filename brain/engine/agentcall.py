"""Fail-loud one-shot agent calls (ARCHITECTURE §4.2).

Rules this module enforces structurally:
  - a failed call RAISES — it never returns "" (v1's empty-return let 37 sessions get marked
    processed-with-0-candidates on 2026-07-09).
  - cap walls raise Capped (pause-and-resume semantics for the caller's ledger loop).
  - model is pinned EXPLICITLY on every call (full id, never an alias, never the account default:
    unpinned headless runs drift to whatever the account default is — the 2026-07-09 fleet reset).
    OPUS 4.8 per the operator's 2026-07-11 fleet-wide directive ("all agents on opus-4-8 until I say
    switch the pin"). Fable 5 was weekly-capped on 2/3 accounts and stalled the seed; Opus works on
    all accounts, so the historical backfill (seed) can finalize. example-confined is the only Fable exception,
    and that's the interactive confined workspace, not this engine.
"""
import json
import os
import re
import subprocess
import time

from . import AGENTS, BRAIN, setting

HOME = os.path.expanduser("~")
ACCOUNTS = f"{AGENTS}/accounts"
CALL_TIMEOUT = 300
PACE_SECONDS = 2          # gap between calls — be gentle on the account
MODEL = setting("BRAIN_MODEL", "claude-opus-4-8")   # exact model id — one choke point; see docs/BRAIN.md
ENGINE_USAGE_LOG = f"{BRAIN}/state/usage/engine-calls.jsonl"

CAP_RE = re.compile(
    r"reached your .{0,20}limit|usage limit|out of usage credits|rate limit|"
    r"quota (exceeded|reached)|hit your .{0,20}limit|session limit|not logged in",
    re.I)


class Capped(Exception):
    """Account capped / session limit — the run should checkpoint and stop, resumable."""


class AgentFailure(Exception):
    """Per-unit call failure (timeout, empty/garbage output) — caller may retry the unit.
    Systemic CLI failures (nonzero exit: auth, unknown cap wordings, API errors) raise Capped
    instead, deliberately: they must PAUSE the whole run resumably, not burn through units."""
    def __init__(self, msg, retryable=False):
        super().__init__(msg)
        self.retryable = retryable


def _rotation_labels():
    """Return the configured fleet account labels; never invent a fallback identity."""
    try:
        with open(f"{ACCOUNTS}/.rotation") as f:
            labels = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        labels = []
    return labels


def _profile_dir(label):
    """Resolve one named rotation profile or fail before a headless call starts."""
    if not label or label == "host" or label not in _rotation_labels():
        raise RuntimeError(f"invalid fleet account label: {label!r}")
    path = f"{ACCOUNTS}/{label}"
    if not (os.path.isdir(path) and os.path.isfile(f"{path}/.credentials.json") and
            os.path.isfile(f"{path}/.claude.json")):
        raise RuntimeError(f"fleet account profile is incomplete: {label}")
    return path


def _cfg_dir():
    """Which named fleet account this load runs on.

    Default is the fleet-active pointer. BRAIN_ACCOUNT may explicitly name another configured
    rotation profile. BRAIN_CFG remains narrowly compatible only when it is exactly a configured
    profile directory; raw paths (including ~/.claude) are rejected.
    """
    requested = os.environ.get("BRAIN_ACCOUNT")
    legacy_cfg = os.environ.get("BRAIN_CFG")
    if requested and legacy_cfg:
        raise RuntimeError("set only one of BRAIN_ACCOUNT or BRAIN_CFG")
    if legacy_cfg:
        for label in _rotation_labels():
            if os.path.realpath(legacy_cfg) == os.path.realpath(f"{ACCOUNTS}/{label}"):
                requested = label
                break
        else:
            raise RuntimeError("BRAIN_CFG must name a configured profile; use BRAIN_ACCOUNT=<label>")
    if not requested:
        try:
            requested = open(f"{ACCOUNTS}/.active").read().strip()
        except OSError:
            requested = ""
    return _profile_dir(requested)


def _parse_envelope(stdout):
    """First JSON object from `--output-format json` stdout, or None if unparseable (a trailing
    stderr/warning line after the object is tolerated — raw_decode stops at the first value)."""
    s = stdout.lstrip()
    if not s.startswith("{"):
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _mark_fable_cap(account, model, wall_text):
    """Report a Fable MODEL-cap wall to rotation (bin/fable-cap-mark). The usage endpoint reads
    healthy while Fable calls wall, so account-watch is blind without this event — the engine is
    Fable-pinned and was stalling on accounts rotation considered fine (2026-07-10). Best-effort:
    accounting/observability must never break a pipeline call."""
    if "fable" not in (model or "") or "limit" not in (wall_text or "").lower():
        return
    try:
        subprocess.run([f"{AGENTS}/bin/fable-cap-mark", account],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def _log_engine_call(sid, model, account, stage, usage):
    """Append one deployed-call usage record for the usage pipeline (best-effort; never fatal —
    accounting must not be able to break a pipeline call)."""
    try:
        rec = {"msg_id": f"engine-{sid}", "model": model, "account": account, "stage": stage,
               "workspace": "brain", "usage": usage,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        os.makedirs(os.path.dirname(ENGINE_USAGE_LOG), exist_ok=True)
        with open(ENGINE_USAGE_LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def call(prompt, *, model=MODEL, timeout=CALL_TIMEOUT, stage=""):
    """One-shot call. Returns the reply text. Raises Capped or AgentFailure — never returns '' on
    failure. Uses --output-format json so each call's token usage is captured for accounting;
    a model-specific cap (e.g. a Fable limit) surfaces as is_error even with exit 0, so cap
    detection reads the envelope, not just the exit code."""
    env = dict(os.environ)
    for k in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH"):
        env.pop(k, None)
    cfg = _cfg_dir()
    env["CLAUDE_CONFIG_DIR"] = cfg
    account = os.path.basename(cfg)
    sid = open("/proc/sys/kernel/random/uuid").read().strip()
    try:
        # prompt via STDIN (chunks exceed ARG_MAX); --no-session-persistence so engine calls
        # never litter the very session store the pipelines harvest.
        r = subprocess.run(
            ["claude", "-p", "--model", model, "--session-id", sid,
             "--no-session-persistence", "--output-format", "json"],
            input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise AgentFailure(f"claude -p timed out after {timeout}s", retryable=True)
    except FileNotFoundError:
        # launcher env bug (a systemd unit's PATH lacks ~/.local/bin — this has bitten us more than once;
        # first launch on 07-10). Name it, so the red run record diagnoses itself.
        raise Capped("claude CLI not on PATH — launcher forgot ~/.local/bin (systemd env bug); "
                     "run is resumable once the launcher is fixed")
    env_obj = _parse_envelope(r.stdout or "")
    if env_obj is not None:
        usage = env_obj.get("usage")
        if usage:
            _log_engine_call(sid, model, account, stage, usage)
        text = env_obj.get("result")
        text = text if isinstance(text, str) else ""
        # is_error=True carries the wall text in `result` (returncode is often 0 in json mode):
        # a model-specific cap (Fable limit) lands here even when the account's 5h/7d has headroom.
        if env_obj.get("is_error") and CAP_RE.search(text or ""):
            _mark_fable_cap(account, model, text)
            raise Capped(f"{account}/{model}: {text[:180]}")
        if env_obj.get("is_error"):
            raise AgentFailure(f"claude -p error: {text[:180]}", retryable=True)
        if not (text or "").strip():
            raise AgentFailure("claude -p returned empty result", retryable=True)
        time.sleep(PACE_SECONDS)
        return text
    # envelope did not parse — fall back to raw-text handling (older CLI / malformed output)
    out = (r.stdout or "") + (r.stderr or "")
    if CAP_RE.search(out) and len(out) < 400:
        raise Capped(out[:200])
    if r.returncode != 0:
        raise Capped(f"claude -p exited {r.returncode}: {out[:180]}")
    if not (r.stdout or "").strip():
        raise AgentFailure("claude -p returned empty output", retryable=True)
    time.sleep(PACE_SECONDS)
    return r.stdout


def call_json(prompt, *, shape=list, retry=1, **kw):
    """call() + extract the first JSON value of `shape` (list or dict) from the reply.
    On parse failure, re-asks up to `retry` times; then raises AgentFailure."""
    opener, closer = ("[", "]") if shape is list else ("{", "}")
    pat = re.compile(re.escape(opener) + r".*" + re.escape(closer), re.S)
    last = ""
    for attempt in range(retry + 1):
        out = call(prompt if attempt == 0 else
                   prompt + "\n\nREMINDER: return ONLY the JSON, no prose, no code fences.", **kw)
        last = out
        m = pat.search(out)
        if m:
            try:
                v = json.loads(m.group(0))
                if isinstance(v, shape):
                    return v
            except json.JSONDecodeError:
                pass
    raise AgentFailure(f"no parseable JSON {shape.__name__} in reply: {last[:160]!r}", retryable=True)
