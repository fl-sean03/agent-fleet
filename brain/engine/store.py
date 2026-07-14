"""Memory stores, auto-memories, STATE.md (ARCHITECTURE §4.5, §5).

The consolidated store layout is `memory/<workspace>/` in this repo (symlinked back from
`~/.claude/projects/<enc>/memory`). `memory/_map.json` records workspace ↔ enc ↔ root and is
written by the consolidation migration; store_dir() creates store dirs for new workspaces on demand.

Routing rule (changed from v1): workspace-scoped knowledge goes to THAT workspace's store — v1 wrote
everything into main's store, so e.g. ws-gamma lessons were never injected into ws-gamma sessions.
"""
import datetime
import glob
import json
import os
import re

from . import MEMORY_ROOT
from .transcripts import norm

AUTO_BEGIN = "<!-- AUTO-HARVEST:BEGIN (maintained by brain — do not hand-edit this block) -->"
AUTO_END = "<!-- AUTO-HARVEST:END -->"

STATE_SECTIONS = [   # (key, heading) — ARCHITECTURE §5, the STATE.md structure
    ("facts", "## Verified facts"),
    ("rules", "## General rules"),
    ("failures", "## Open failures"),
    ("lessons", "## Lessons learned"),
    ("resume", "## Last session"),
]
STATE_POINTER = ("> **Read [STATE.md](STATE.md) before working** — verified facts · rules · "
                 "open failures · resume pointer.")


def _map_path():
    return f"{MEMORY_ROOT}/_map.json"


def load_map():
    try:
        with open(_map_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_map(m):
    os.makedirs(MEMORY_ROOT, exist_ok=True)
    tmp = _map_path() + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(m, fh, indent=1, sort_keys=True)
    os.replace(tmp, _map_path())


def store_dir(ws_name, enc=None, root=None):
    """memory/<ws>/ — created (and recorded in _map.json) if missing, and WIRED into the live
    session path (`~/.claude/projects/<enc>/memory` symlink) so knowledge written here is actually
    injected. A store without that symlink is write-only — 11 of 20 stores were born orphaned
    before the 2026-07-10 audit caught it."""
    m = load_map()
    # An enc-shaped name (unknown-enc fallback: the name IS the enc) must not spawn a duplicate
    # store when the map already knows that enc under a canonical name — redirect to it. (2026-07-10:
    # consult created memory/-home-operator-work-agents-ws-alpha alongside ws-alpha-parent.)
    if ws_name.startswith("-"):
        for known, entry in m.items():
            if entry.get("enc") == ws_name and known != ws_name:
                return store_dir(known)
        enc = enc or ws_name   # self-enc: an enc-shaped store wires to its own enc
    d = f"{MEMORY_ROOT}/{ws_name}"
    if ws_name not in m or (enc and not m[ws_name].get("enc")):
        m[ws_name] = {"enc": enc or m.get(ws_name, {}).get("enc"),
                      "root": root or m.get(ws_name, {}).get("root")}
        save_map(m)
    os.makedirs(d, exist_ok=True)
    _ensure_wired(ws_name, m.get(ws_name, {}).get("enc"), d)
    return d


def _ensure_wired(ws_name, enc, d):
    """Symlink the live memory path at this store (idempotent). Unknown enc → resolve from
    descriptors; still unknown → nothing to wire (unknown-enc stores are reachable only if their
    enc is recorded). An existing REAL dir is left alone (needs a merge, not a clobber) — callers
    see it in `brain status` via audit_wiring()."""
    home = os.path.expanduser("~")
    if not enc:
        from .sessions import descriptor_map
        for e, w in descriptor_map().items():
            if w.name == ws_name:
                enc = e
                m = load_map()
                m.setdefault(ws_name, {})["enc"] = e
                save_map(m)
                break
    if not enc:
        return
    live = f"{home}/.claude/projects/{enc}/memory"
    if os.path.islink(live) or os.path.isdir(live):
        return
    os.makedirs(os.path.dirname(live), exist_ok=True)
    os.symlink(os.path.abspath(d), live)


def audit_wiring():
    """[(ws, problem)] for stores whose live injection path is missing or a real dir (unwired)."""
    home = os.path.expanduser("~")
    out = []
    m = load_map()
    for ws in sorted(os.listdir(MEMORY_ROOT) if os.path.isdir(MEMORY_ROOT) else []):
        if ws.startswith("_") or not os.path.isdir(f"{MEMORY_ROOT}/{ws}"):
            continue
        enc = m.get(ws, {}).get("enc")
        if not enc:
            out.append((ws, "no enc recorded — unreachable by sessions"))
            continue
        live = f"{home}/.claude/projects/{enc}/memory"
        if os.path.islink(live):
            continue
        out.append((ws, "real dir shadows store — merge needed" if os.path.isdir(live)
                    else "symlink missing"))
    return out


def verify_quote(quote, source_text):
    """The anti-fabrication gate: quote must literally exist (whitespace-normalized) in source."""
    q = norm(quote)
    return bool(q) and q in norm(source_text)


# ---------------------------------------------------------------- auto-memories
def write_auto_memory(ws, mem, provenance, dry=False, writes_log=None):
    """Write auto_<type>_<slug>.md into ws's store. `provenance` = [(quote, resolved_candidate|None)].
    Returns filename or None (a memory with no resolvable evidence is not written)."""
    typ = mem.get("type", "project")
    if typ not in ("project", "feedback", "reference"):
        typ = "project"
    slug = re.sub(r"[^a-z0-9]+", "-", mem["name"].lower()).strip("-")
    name = f"auto_{typ}_{slug}"
    today = datetime.date.today().isoformat()
    body = ["---",
            f"name: {name.replace('_', '-')}",
            f"description: {mem.get('description', '').strip()}",
            "metadata:",
            "  node_type: memory",
            f"  type: {typ}",
            "  source: brain",
            f"  harvested: {today}",
            f"  workspace: {ws}",
            "---",
            ""]
    stmt = mem["statement"].strip()
    if mem.get("contradiction"):
        stmt = f"**⚠ CONTRADICTION with [[{mem['contradiction']}]]:** " + stmt
    body.append(stmt)
    if typ == "feedback" and mem.get("why"):
        body += ["", f"**Why:** {mem['why'].strip()}"]
    body += ["", "## Provenance",
             f"_Auto-harvested {today} from the fleet's own conversations._"]
    seen = set()
    for q, c in provenance:
        if not q or norm(q) in seen:
            continue
        seen.add(norm(q))
        if c:
            body.append(f"- session `{c.get('_session', '?')[:8]}` ({c.get('_workspace', '?')}, "
                        f"{c.get('turn_ts', '')}): \"{q[:280]}\"")
        else:
            body.append(f"- (workspace {ws}): \"{q[:280]}\"")
        if len(seen) >= 6:
            break
    if not seen:
        return None
    if dry:
        return name + ".md"
    d = store_dir(ws)
    with open(f"{d}/{name}.md", "w") as fh:
        fh.write("\n".join(body) + "\n")
    if writes_log:
        os.makedirs(os.path.dirname(writes_log), exist_ok=True)
        with open(writes_log, "a") as fh:
            fh.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
                                 "file": f"{name}.md", "workspace": ws, "type": typ,
                                 "contradiction": mem.get("contradiction", ""),
                                 "description": mem.get("description", "")}) + "\n")
    refresh_index_block(ws)
    return name + ".md"


def existing_index(ws):
    """names + descriptions of memory files in ws's store (human + auto) — synth dedup input."""
    lines = []
    d = f"{MEMORY_ROOT}/{ws}"
    for p in sorted(glob.glob(f"{d}/*.md")):
        base = os.path.basename(p)
        if base in ("MEMORY.md", "STATE.md"):
            continue
        desc = ""
        try:
            m = re.search(r"^description:\s*(.+)$", open(p).read(), re.M)
            desc = m.group(1).strip() if m else ""
        except Exception:
            pass
        lines.append(f"- {base}: {desc}")
    return "\n".join(lines)


def refresh_index_block(ws):
    """Rewrite ONLY the AUTO-HARVEST block in ws's MEMORY.md (+ ensure the STATE.md pointer is the
    first content line). Human-authored lines are never edited."""
    d = store_dir(ws)
    auto = sorted(glob.glob(f"{d}/auto_*.md"))
    lines = [AUTO_BEGIN, "## Auto-harvested (provenance-grounded, from past conversations)"]
    for p in auto:
        base = os.path.basename(p)
        desc = ""
        try:
            m = re.search(r"^description:\s*(.+)$", open(p).read(), re.M)
            desc = m.group(1).strip() if m else ""
        except Exception:
            pass
        lines.append(f"- [{base}]({base}) - {desc}")
    lines.append(AUTO_END)
    block = "\n".join(lines)
    idx = f"{d}/MEMORY.md"
    txt = open(idx).read() if os.path.exists(idx) else "# Memory index\n"
    # legacy block marker (v1 wording) is migrated to the current one
    legacy = AUTO_BEGIN.replace("brain", "memory-harvest")
    if legacy in txt:
        txt = txt.replace(legacy, AUTO_BEGIN)
    if AUTO_BEGIN in txt and AUTO_END in txt:
        # replacement via lambda: a plain-string replacement re-interprets \-escapes in the block
        # content (re.error / silent duplication) — 2026-07-09 verifier finding
        txt = re.sub(re.escape(AUTO_BEGIN) + r".*?" + re.escape(AUTO_END),
                     lambda _m: block, txt, flags=re.S)
    else:
        txt = txt.rstrip() + "\n\n" + block + "\n"
    if os.path.exists(f"{d}/STATE.md") and STATE_POINTER.splitlines()[0] not in txt:
        head, _, rest = txt.partition("\n")
        txt = f"{head}\n\n{STATE_POINTER}\n{rest.lstrip()}" if head.startswith("#") \
            else f"{STATE_POINTER}\n\n{txt}"
    with open(idx, "w") as fh:
        fh.write(txt)


# ---------------------------------------------------------------- STATE.md
def _auto_markers(key):
    return f"<!-- AUTO:{key} BEGIN -->", f"<!-- AUTO:{key} END -->"


def _state_template(ws):
    parts = [f"# STATE — {ws}",
             "_System-maintained nightly (inside the AUTO blocks) + agent-writable (anything outside "
             "them persists). Read at session start; update before session end._", ""]
    for key, heading in STATE_SECTIONS:
        b, e = _auto_markers(key)
        parts += [heading, b, "(nothing yet)", e, ""]
    return "\n".join(parts)


def state_path(ws):
    return f"{store_dir(ws)}/STATE.md"


def state_read(ws):
    """{section_key: current AUTO-block content} (missing file/blocks → empty)."""
    try:
        txt = open(state_path(ws)).read()
    except OSError:
        return {}
    out = {}
    for key, _ in STATE_SECTIONS:
        b, e = _auto_markers(key)
        m = re.search(re.escape(b) + r"\n?(.*?)\n?" + re.escape(e), txt, re.S)
        if m:
            out[key] = m.group(1).strip()
    return out


def state_write(ws, sections, dry=False):
    """Replace the given sections' AUTO blocks; everything else in the file is preserved.
    Missing file → created from template. Missing markers (agent deleted them) → section re-appended."""
    path = state_path(ws)
    txt = open(path).read() if os.path.exists(path) else _state_template(ws)
    for key, content in sections.items():
        if key not in {k for k, _ in STATE_SECTIONS}:
            raise ValueError(f"unknown STATE section {key!r}")
        b, e = _auto_markers(key)
        block = f"{b}\n{content.strip() or '(nothing yet)'}\n{e}"
        if b in txt and e in txt:
            # lambda replacement — promoted text can contain \-sequences (see refresh_index_block)
            txt = re.sub(re.escape(b) + r".*?" + re.escape(e), lambda _m: block, txt, flags=re.S)
        else:
            heading = dict(STATE_SECTIONS)[key]
            txt = txt.rstrip() + f"\n\n{heading}\n{block}\n"
    if dry:
        return txt
    with open(path, "w") as fh:
        fh.write(txt)
    refresh_index_block(ws)   # ensures the MEMORY.md pointer exists once STATE.md does
    return txt
