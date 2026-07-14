"""brain.engine — shared library for the second-brain system.

Import-side-effect free. Every path is derived from CONFIGURATION, never hardcoded, so the kit
works on any machine and any user. Resolution order for each setting:

    1. environment variable   (FLEET_KIT_ROOT, FLEET_CONFINED_ROOT, BRAIN_MODEL, ...)
    2. ~/.agents/fleet.conf   (KEY=VALUE lines, written by install.sh)
    3. the default below

FLEET_KIT_ROOT is this repo's checkout — brain/ code plus the memory/ stores and brain state live
under it, so a machine's knowledge travels with its checkout. See docs/BRAIN.md.
"""
import os

HOME = os.path.expanduser("~")
AGENTS = os.path.expanduser(os.environ.get("FLEET_AGENTS_DIR", f"{HOME}/.agents"))
CONF = f"{AGENTS}/fleet.conf"


def _conf():
    """Parse ~/.agents/fleet.conf → dict. A missing or garbled file is never fatal."""
    d = {}
    try:
        with open(CONF) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return d


_C = _conf()


def setting(key, default):
    """env > fleet.conf > default (with ~ expanded)."""
    return os.path.expanduser(os.environ.get(key) or _C.get(key) or default)


# --- roots ---------------------------------------------------------------------------------
PROJ = setting("FLEET_KIT_ROOT", f"{HOME}/agent-fleet")   # this repo's checkout
BRAIN = f"{PROJ}/brain"
MEMORY_ROOT = f"{PROJ}/memory"          # per-workspace knowledge stores (memory/_map.json)
STAGED = f"{BRAIN}/staged"
REVIEWS = f"{BRAIN}/reviews"
STATE = f"{BRAIN}/state"                # ledger/, runs/, health.json


# --- the confinement wall ------------------------------------------------------------------
# Session-store directories are named by encoding a workspace ROOT (every non-alphanumeric → '-').
# The brain harvests ONLY workspaces under the operator's home, and NEVER anything under the
# confined root: confined-workspace content must not enter the shared knowledge base. Both
# prefixes are DERIVED from the configured roots, so the wall is correct on any machine instead
# of depending on one author's home path.
def enc_path(path):
    """ROOT → session-store encoding (non-alphanumerics collapse to '-')."""
    return "".join(c if c.isalnum() else "-" for c in path)


CONFINED_ROOT = setting("FLEET_CONFINED_ROOT", f"{HOME}/confined")
INCLUDE_PREFIX = enc_path(HOME)                    # e.g. "-home-alice"
CONFINED_PREFIX = enc_path(CONFINED_ROOT) + "-"    # e.g. "-home-alice-confined-"  (NEVER harvested)
