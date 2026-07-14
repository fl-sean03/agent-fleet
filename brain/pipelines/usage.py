"""usage pipeline — scan every in-scope transcript + the engine-call log into state/usage/usage.db.

Incremental: a file whose (mtime,size) is unchanged since the last scan is skipped. Re-scanning a
changed file is safe — rows are INSERT OR IGNORE on the API message id, so streaming's repeated
usage lines and live/archive overlap both dedupe automatically.

CONFINEMENT WALL: the same exclusions as engine/sessions.py (CONFINED_PREFIX, out-of-scope, junk). Confined workspace
transcripts never enter this DB — only fleet spend is accounted here.
"""
import glob
import json
import os

from engine import setting, usagedb
from engine.runlog import Run
from engine.sessions import (CONFINED_PREFIX, INCLUDE_PREFIX, LIVE_STORE, ARCHIVE_STORE,
                             is_junk_enc, descriptor_map, workspace_of)

HOME = os.path.expanduser("~")
SESSION_ARCHIVE = f"{HOME}/.agents/session-archive"   # session-guard mirrors each account store here
ROTATION = f"{HOME}/.agents/accounts/.rotation"
# OPTIONAL: an archive of session stores from a previous machine, if you migrated one in.
OLD_HISTORY = setting("FLEET_OLD_HISTORY", "")   # empty = none (the default)


WALK_PRUNE = ("node_modules", "site-packages", ".pnpm", ".next", "proxy-venv",
              ".git", "/dist", "/build", ".venv")
# project-local .claude stores (a project keeping its own .claude dir) survived Claude Code's ~30-day transcript pruning
# and carry per-message usage back to 2026-01 — the fleet store itself only goes back to 2026-04.
# Bounded discovery walk for project-local .claude stores. Configure to YOUR code root(s);
# empty disables the walk entirely.
WALK_BASES = tuple(p for p in setting("FLEET_WALK_BASES", f"{HOME}/work").split(":") if p)


def _fleet_labels():
    try:
        return [ln.strip() for ln in open(ROTATION)
                if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def _scan_roots():
    """[(root, forced_category, ws_label)] — every dir to glob `<root>/<enc>/*.jsonl` from.

    forced_category='confined' pins confined-workspace stores (their generic enc '-work' carries no confined workspace marker,
    so the STORE location is the signal); ws_label pins the confined workspace's name. Everything else is
    inferred per-enc by _categorize. Coverage:
      - live store + archive .claude layer            (current fleet)
      - session-archive/<fleet-account>               (reaped/idle workspaces survive only here)
      - session-archive/{example-confined-2,example-confined} + confined-cfg/<c>  (confined-workspace usage — token counts only, no content)
      - an optional archived history root from a previous machine (FLEET_OLD_HISTORY)
      - any *other* .claude/projects under ~/work     (project-local stores back to 2026-01)
    Processed once, then cheap: scan_state skips unchanged files, so nightly cost is a stat per file.
    """
    roots = [(LIVE_STORE, None, None), (ARCHIVE_STORE, None, None)]
    fleet = set(_fleet_labels())
    seen = {os.path.realpath(LIVE_STORE), os.path.realpath(ARCHIVE_STORE)}

    def add(root, cat, label):
        rp = os.path.realpath(root)
        if os.path.isdir(root) and rp not in seen and glob.glob(f"{root}/*/*.jsonl"):
            seen.add(rp)
            roots.append((root, cat, label))

    for p in sorted(glob.glob(f"{SESSION_ARCHIVE}/*")):
        name = os.path.basename(p)
        if name in fleet:
            add(p, None, None)                       # fleet account mirror — infer per enc
        elif name.startswith(".") or name == "_scrollback":
            continue
        else:
            add(p, "confined", name)                   # a confined workspace archive
    for p in sorted(glob.glob(f"{HOME}/.agents/confined-cfg/*/projects")):
        add(p, "confined", os.path.basename(os.path.dirname(p)))
    for pr in (sorted(glob.glob(f"{OLD_HISTORY}/*/.claude*/projects")) +
               sorted(glob.glob(f"{OLD_HISTORY}/*/projects"))) if OLD_HISTORY else []:
        add(pr, None, None)
    # bounded discovery walk: catch scattered project-local .claude stores (project-local stores)
    for base in WALK_BASES:
        for dp, dirs, _files in os.walk(base):
            if any(x in dp for x in WALK_PRUNE):
                dirs[:] = []
                continue
            if os.path.basename(dp) == "projects" and ".claude" in dp:
                add(dp, None, None)
    return roots


def _iter_usage_lines(path):
    """Yield (msg_id, ts, model, usage_dict, is_sidechain) for assistant lines carrying usage."""
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line or '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage")
            mid = msg.get("id")
            if not usage or not mid:
                continue
            yield (mid, d.get("timestamp"), msg.get("model"),
                   usage, bool(d.get("isSidechain")))


def _row_from_usage(usage):
    """(input, cache_read, cw5m, cw1h, output, web_search, web_fetch) from a usage block."""
    cc = usage.get("cache_creation") or {}
    st = usage.get("server_tool_use") or {}
    return (int(usage.get("input_tokens") or 0),
            int(usage.get("cache_read_input_tokens") or 0),
            int(cc.get("ephemeral_5m_input_tokens") or 0),
            int(cc.get("ephemeral_1h_input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            int(st.get("web_search_requests") or 0),
            int(st.get("web_fetch_requests") or 0))


def _categorize(enc, hint):
    """Category for one enc: 'confined' | 'real' | None(skip).

    This is a SPEND ledger — deliberately broader than the knowledge-harvest scope
    (engine/sessions.py). A confined workspace's USAGE is counted here because it is token-count
    METADATA, never content: the harvest/review/consult content wall is untouched. Skips (return
    None, counted — never silent): test/bench/worktree/tmp/scratchpad junk, foreign non-home paths."""
    if hint == "confined" or enc.startswith(CONFINED_PREFIX):
        return "confined"
    if is_junk_enc(enc):
        return None                        # test/bench/worktree/tmp noise — skip (small), reported
    if enc.startswith(INCLUDE_PREFIX) or hint == "real":
        return "real"                      # the operator's own work
    return None                            # foreign / unrecognized — skip, reported


def _ws_name(enc, dmap, category, label):
    """Readable workspace label. Confined workspace stores → the confined workspace name (identity, not content). Known encs
    resolve via descriptors; unknown OLD-box encs get a de-prefixed name
    (`-home-operator-Workspace-ws-registry` → `ws-registry`) instead of the raw enc."""
    if category == "confined":
        return f"confined workspace:{label}" if label else "confined workspace:" + enc.replace("-home-operator-confined workspaces-", "")
    w = workspace_of(enc, dmap)
    if w.kind != "unknown":
        return w.name
    s = enc
    for pre in ("-home-operator-Workspace-", "-home-operator-work-", "-home-operator-", "-home-operator-"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    for cut in ("--claude-worktrees", "--worktrees", "--ws-worktrees"):
        if cut in s:
            s = s.split(cut)[0]
    return s or enc


def _scan_transcripts(db, timeline, run):
    dmap = descriptor_map()
    cur = db.cursor()
    scanned = {p: (mt, sz) for p, mt, sz in cur.execute("SELECT path,mtime,size FROM scan_state")}
    inserted = 0
    files = 0
    for store, hint, label in _scan_roots():
        for path in glob.glob(f"{store}/*/*.jsonl"):
            enc = os.path.basename(os.path.dirname(path))
            category = _categorize(enc, hint)
            if category is None:
                run.count("skipped_junk_files", 1)
                continue
            try:
                stt = os.stat(path)
            except OSError:
                continue
            prev = scanned.get(path)
            if prev and abs(prev[0] - stt.st_mtime) < 1e-6 and prev[1] == stt.st_size:
                continue   # unchanged since last scan
            files += 1
            run.count(f"cat_{category}_files", 1)
            ws = _ws_name(enc, dmap, category, label)
            session = os.path.basename(path)[:-6]
            for mid, ts, model, usage, sidechain in _iter_usage_lines(path):
                model = usagedb.norm_model(model)
                inp, cread, cw5m, cw1h, out, wsr, wfr = _row_from_usage(usage)
                agent = "subagent" if sidechain else "main"
                account = usagedb._account_at(ts, timeline)
                day = (ts or "")[:10]
                cur.execute(
                    "INSERT OR IGNORE INTO usage(msg_id,ts,day,workspace,enc,session,model,agent,"
                    "category,account,input_tokens,cache_read_tokens,cache_write_5m_tokens,"
                    "cache_write_1h_tokens,output_tokens,web_search,web_fetch,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'transcript')",
                    (mid, ts, day, ws, enc, session, model, agent, category, account,
                     inp, cread, cw5m, cw1h, out, wsr, wfr))
                inserted += cur.rowcount
            cur.execute("INSERT OR REPLACE INTO scan_state(path,mtime,size) VALUES(?,?,?)",
                        (path, stt.st_mtime, stt.st_size))
    db.commit()
    run.count("transcript_files_scanned", files)
    run.count("transcript_rows_new", inserted)


def _scan_engine_log(db, run):
    """Ingest agentcall's engine-calls.jsonl. Each line already carries its account + model."""
    path = usagedb.ENGINE_LOG
    if not os.path.exists(path):
        return
    cur = db.cursor()
    inserted = 0
    with open(path, errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mid = d.get("msg_id")
            usage = d.get("usage")
            if not mid or not usage:
                continue
            inp, cread, cw5m, cw1h, out, wsr, wfr = _row_from_usage(usage)
            ts = d.get("ts")
            cur.execute(
                "INSERT OR IGNORE INTO usage(msg_id,ts,day,workspace,enc,session,model,agent,"
                "category,account,input_tokens,cache_read_tokens,cache_write_5m_tokens,"
                "cache_write_1h_tokens,output_tokens,web_search,web_fetch,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'engine')",
                (mid, ts, (ts or "")[:10], d.get("workspace", "brain"), "", d.get("stage", ""),
                 usagedb.norm_model(d.get("model")), "brain-engine", "real", d.get("account"),
                 inp, cread, cw5m, cw1h, out, wsr, wfr))
            inserted += cur.rowcount
    db.commit()
    run.count("engine_rows_new", inserted)


def run(dry=False):
    with Run("usage") as r:
        db = usagedb.connect()
        timeline = usagedb.account_timeline()
        _scan_transcripts(db, timeline, r)
        _scan_engine_log(db, r)
        total = db.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        r.count("rows_total", total)
        db.close()
    return r


if __name__ == "__main__":
    import sys
    run(dry="--dry" in sys.argv)
