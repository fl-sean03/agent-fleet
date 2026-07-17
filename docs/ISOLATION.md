# Isolation

## Why this exists (read this part)

An agentic CLI asked for a fact it was never told does not politely give up. It **searches the
filesystem with its tools.** In a controlled experiment on the fleet this kit came from, an
unsandboxed agent asked about a fact from a *different* agent's session did exactly this:

```
rg -i "widget WX" ~/.grok/sessions /tmp
```

…found the answer in a **sibling agent's transcript**, and reported it confidently. It wasn't model
confusion or context bleed — the on-disk session store was correctly per-directory. The agent simply
read someone else's conversation because that was the cheapest path to an answer.

Measured, on the same host, running the agent's exact search:

| | hits |
|---|---|
| unsandboxed | **149** (finds other agents' session content) |
| confined (this kit's sandbox) | **0** |

If any of your agents touch work that must not leak into other work — client engagements, regulated
data, credentials, anything under NDA — **prompt instructions are not a boundary.** A namespace is.

## The three tiers

### Project (default)
Full host access, shared session store, host login. Right for your own work where cross-visibility is
fine or useful.

```bash
agentctl new api --root ~/work/api --up
```

### Confined
A **bubblewrap namespace**. The agent sees:

- `/work` → its own workspace directory (bind-mounted)
- `/config` → its own isolated Claude config (its **own credentials**, its **own session store**)
- a fresh `/tmp` (tmpfs), plus read-only `/usr`, certs, and the CLI binary

It does **not** see: your home directory, your other workspaces, your other agents' transcripts,
your credentials, `/etc/passwd`. Not "denied" — **not mounted**. There is nothing to escalate to.

```bash
agentctl new exampleco --confined "ExampleCo Corp" --up
agentctl login exampleco        # its own OAuth; never touches your other accounts' credentials
```

Because the namespace *is* the wall, the agent inside can run with permissions relaxed — the
boundary doesn't depend on the agent's good behavior.

Its credentials are stored per-account under `~/.agents/confined-cfg/<name>/` and are **never** a
project account's live credential. A confined workspace can be pinned to one account permanently
(`.pinned-account`), so a fleet-wide rotation never drags it across an account boundary it shouldn't
cross.

#### One engagement, several agents (the multi-project client pattern)

Sometimes one client engagement grows a second project — a build repo and a docs site, say — and you
want a second agent without a second login. The supported pattern: give both descriptors the **same
ROOT** (the engagement root). The confined launcher keys the isolated config on the ROOT's basename,
so both workspaces mount the **same** `~/.agents/confined-cfg/<engagement>/` at `/config`:

```
# projects/exampleco.env                    # projects/exampleco-site.env
ROOT="$HOME/confined/exampleco"             ROOT="$HOME/confined/exampleco"     # same engagement root
KIND="confined"                             KIND="confined"
SESSION_ID="<uuid-a>"                       SESSION_ID="<uuid-b>"   # distinct pin = distinct conversation
```

Three things make this correct, and one of them is subtle:

- **The config + credential file is a share, not a copy.** Load-bearing: the CLI rename-writes
  `.credentials.json` on every token refresh, and refresh tokens are single-use and rotating — two
  *copies* would fork the refresh token, and whichever copy refreshes second kills the other's
  login. One shared file means both agents ride the one rotating credential together. (Same rule
  that forbids copying credentials into composed profile dirs — see `docs/PROFILES.md`.)
- **Distinct `SESSION_ID`s** keep the two conversations separate inside the shared isolated store.
- **The isolation boundary is the client engagement, not the sub-project.** Both agents see the
  whole engagement root at `/work` (and each other's transcripts in the shared store) — and nothing
  outside it. That is the right wall for "two projects, one client". A second *client* is a
  different boundary and gets its own confined workspace, config, and login.

### Remote
The agent process runs on another machine over `ssh -t`; the local pane is a view. Its auth lives on
the remote box, so account swaps don't apply — and the fleet's swap logic **skips these by design**
(bouncing them would just kill a remote agent mid-work).

```bash
ROOT="$HOME/work/thing"
AGENTS="claude-remote"
SSH="user@remote-host"
```

## The second wall: the knowledge base

If you enable the brain, there's a second place confined content could leak: the nightly pipeline
that reads transcripts and writes shared memory. So the wall exists there too, **structurally**.

The session-store directory for a workspace is its ROOT with every non-alphanumeric character turned
into `-`. Which produces a trap:

```
home            /home/alice           →  -home-alice          (the include prefix)
confined work   /home/alice/confined/exampleco  →  -home-alice-confined-exampleco
```

The confined store **starts with the include prefix**. A naive "is it under my home?" test harvests
it. That is a real leak, and it happened once. The fix is a dedicated exclusion checked before the
include test, with both prefixes derived from configuration (never a literal path):

```python
INCLUDE_PREFIX  = enc_path(HOME)                    # -home-alice
CONFINED_PREFIX = enc_path(CONFINED_ROOT) + "-"     # -home-alice-confined-   ← excluded first
```

Every exclusion is **counted**, never silent — a run record shows exactly how many sessions were
walled off. `brain/tests/test_engine.py` asserts the leak case directly, and the test builds its
fixture from the derived prefixes so it stays correct on any machine.

## Choosing

| If the work… | Use |
|---|---|
| is yours, and cross-agent visibility is fine or helpful | project |
| belongs to someone else, or is regulated, or must not leak | **confined** |
| must run on other hardware (GPU box, lab machine) | remote |

When in doubt: confined. The cost is one extra login and a namespace; the alternative is discovering
in an audit that an agent read something it shouldn't have.

## Limits — stated honestly

- Confinement protects the **filesystem**. The agent still has **network access** (it has to reach
  the model API). It can exfiltrate what it can see; it just can't see much.
- `bwrap` must be present and working. If it isn't, `agentctl` will not silently downgrade a confined
  workspace to unconfined — it refuses.
- The brain's wall protects the **shared knowledge base**. If you deliberately point a tool at a
  confined workspace's files yourself, nothing stops you. The wall guards the automated path.
