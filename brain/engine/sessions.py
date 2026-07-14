"""Canonical workspace identity + session enumeration + THE SCOPE WALL (ARCHITECTURE §4.1).

Identity is resolved FORWARD from workspace descriptors (enc = ROOT with non-alnum→'-'); an enc is
never reverse-parsed (reverse-guessing mangles hyphenated dirs: ws-beta-shear → "shear" — the v1 label
bug that also caused the ws-alpha/WsAlpha double-review).

The scope wall lives in exactly one place (enumerate_sessions) and every exclusion is COUNTED, never
silent. Confined-workspace stores are excluded STRUCTURALLY, and this is load-bearing: the include
prefix (the encoded home path) is also a prefix of the encoded confined root, so a naive
"starts with home" test silently harvests confined content. Both prefixes are derived from the
configured roots in engine/__init__ — never a literal home path.
"""
import glob
import os
import re
from collections import namedtuple

from . import AGENTS, HOME, INCLUDE_PREFIX, CONFINED_PREFIX, CONFINED_ROOT  # noqa: F401  (the wall)

LIVE_STORE = os.path.expanduser(os.environ.get("FLEET_SESSION_STORE", f"{HOME}/.claude/projects"))
ARCHIVE_STORE = f"{AGENTS}/session-archive/.claude"
DESC = f"{AGENTS}/projects"
ATTIC = f"{AGENTS}/attic/retired-descriptors"
JUNK_MARKERS = ("scratchpad", "-tmp", "smoke", "fixture", "modeltest", "ztest",
                "test2", "worktrees", "benchmark", "workspaces", "workspace",
                )   # ephemeral/automated run dirs that are not real workspaces


def is_junk_enc(enc):
    """v1 markers, except bare '-test' is anchored (suffix or '-test-') so a REAL project like
    ws-gpu-tests ('...-ws-gpu-tests') is not silently dropped — verifier finding, 2026-07-09."""
    if any(x in enc for x in JUNK_MARKERS):
        return True
    return enc.endswith("-test") or "-test-" in enc
MIN_SESSION_BYTES = 4000

Workspace = namedtuple("Workspace", "name root enc kind")   # kind: project | main | unknown
SessionMeta = namedtuple("SessionMeta", "path enc workspace mtime size")


def _root_of(envf):
    for line in open(envf, errors="ignore"):
        m = re.match(r'\s*ROOT="?([^"]+)"?', line)
        if m:
            return os.path.expandvars(m.group(1)).replace("$HOME", HOME).replace("~", HOME)
    return None


def descriptor_map():
    """enc -> Workspace, forward-mapped from every live + retired descriptor, plus main.
    First (live) descriptor wins over retired duplicates."""
    m = {}
    for envf in glob.glob(f"{DESC}/*.env") + glob.glob(f"{ATTIC}/*.env.retired-*"):
        name = re.split(r"\.env", os.path.basename(envf))[0]
        root = _root_of(envf)
        if not root:
            continue
        enc = re.sub(r"[^a-zA-Z0-9]", "-", root)
        # confined workspaces (example-confined/example-confined-2, roots under $CONFINED_ROOT) are marked so EVERY consumer can
        # honor the wall — review's git/log scan read confined workspace repos through this map (2026-07-09
        # verifier finding); transcripts were already walled in enumerate_sessions.
        kind = "confined" if root.startswith(CONFINED_ROOT) else "project"
        m.setdefault(enc, Workspace(name, root, enc, kind))
    m.setdefault(INCLUDE_PREFIX, Workspace("main", HOME, INCLUDE_PREFIX, "main"))
    return m


def workspace_of(enc, dmap=None):
    """Canonical Workspace for an enc. Unknown encs keep their FULL enc as the name — an ugly
    unambiguous label beats a pretty wrong one (never last-segment)."""
    dmap = dmap if dmap is not None else descriptor_map()
    return dmap.get(enc) or Workspace(enc, None, enc, "unknown")


def enumerate_sessions(counters=None):
    """All in-scope sessions from live store ∪ archive, deduped by session-id (largest wins).
    Returns {sid: SessionMeta}. `counters` (dict) receives exclusion counts by reason —
    pass Run.excluded-backed dict so drops are observable, never silent."""
    dmap = descriptor_map()
    best = {}
    seen_excluded = set()   # count each excluded FILE once, not once per store

    def drop(reason, key):
        if counters is not None and key not in seen_excluded:
            seen_excluded.add(key)
            counters[reason] = counters.get(reason, 0) + 1

    for store in (LIVE_STORE, ARCHIVE_STORE):
        for path in glob.glob(f"{store}/*/*.jsonl"):
            enc = os.path.basename(os.path.dirname(path))
            if enc.startswith(CONFINED_PREFIX):
                drop("confined-wall", path); continue
            if not enc.startswith(INCLUDE_PREFIX):
                drop("out-of-scope", path); continue
            if is_junk_enc(enc):
                drop("junk", path); continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_size < MIN_SESSION_BYTES:
                drop("too-small", path); continue
            sid = os.path.basename(path)[:-6]
            if sid not in best or st.st_size > best[sid].size:
                best[sid] = SessionMeta(path, enc, workspace_of(enc, dmap).name,
                                        int(st.st_mtime), st.st_size)
    return best
