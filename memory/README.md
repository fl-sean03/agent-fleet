# memory/

Per-workspace knowledge stores, written by the brain (see ../docs/BRAIN.md). Ships EMPTY — this is
your fleet's knowledge, and it accrues on your machine.

    memory/
      _map.json          workspace ↔ session-store ↔ root mapping (auto-maintained)
      <workspace>/
        MEMORY.md        the index — one line per memory; THIS is what loads into a session
        STATE.md         curated: verified facts · operating rules · open failures · resume pointer
        auto_*.md        one durable fact each, with a ## Provenance section (session id + verbatim quote)

`STATE.md` is the highest-value file in the system: it is what an agent reads first to answer "what
was I doing?" after a reboot, a swap, or a week away. Write to it by hand too — the brain owns only
its own delimited AUTO blocks and preserves everything else.
