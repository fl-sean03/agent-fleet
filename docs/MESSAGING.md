# Inter-agent messaging

```bash
agentctl send ws-gpu "profile the matmul kernel; report the top 3 hotspots"
agentctl send a,b,c "standup: one line each"
agentctl send @all "pausing for a deploy — checkpoint and idle"
agentctl msglog 20 --from main --since 2h
agentctl flush ws-gpu            # redeliver what queued while it was down
agentctl kick ws-gpu             # MANUAL-ONLY: submit input stranded in its composer (--dry first)
```

## The one design decision

A message is **delivered into the recipient's live conversation as a real turn** — not into an inbox
it has to remember to check.

The first version of this was an inbox: durable, clean, and **invisible**. Agents sat idle on unread
messages for hours; replies were missed. Durable-but-unread is worse than useless, because everyone
*believes* the message was delivered. The rule that came out of it: **actually-delivered beats
durable-but-invisible** — so now the text is submitted into the agent's prompt and it processes it
like anything you typed.

Durability didn't go away; it moved alongside (see the log, below).

## How delivery actually works (v3 — verified at every step)

The mechanics are small but every part is load-bearing:

1. **Target the agent's pane, not the session.** A workspace's tmux session has a control shell in
   pane 1 and the agent in pane 2. Sending to "the session" hits whatever pane is active — usually
   the shell, where your message becomes a shell command nobody ever sees. Deliver to pane 2.
2. **Verify, don't assume.** The v2 mechanism (paste → fixed ~0.8s delay → Enter) still stranded
   directives whenever the pane was at a boot screen or modal. v3 verifies each step:

   ```
   readiness gate      — never paste until a ❯ composer is actually rendered
                         (pasting at a boot screen/modal sends the eventual Enter to the dialog)
   paste-arrival check — fire Enter only once the text is visibly IN the composer
   verified-Enter loop — after each Enter, confirm the composer cleared; retry with
                         escalating waits
   ```

3. **Busy agents are fine.** A recipient mid-turn has the pasted text queued by its harness and
   surfaced to the model as a mid-turn message. You do not need to wait for idle. (The composer
   keeps *rendering* the queued text until the turn ends — that's delivered, not stuck; do not
   re-send.)

**"Stuck" is a distinct, terminal verdict.** Text still visibly sitting in the composer after all
retries is IN the box, so it is never re-queued (that double-delivers). Stranded composer text is
**left visible for the operator — never auto-submitted**. The reason is a hard one: the Remote
Control bridge can **replay an unsent draft into a composer on reconnect** (observed live: the same
draft re-appeared three times in one afternoon), so any automation that presses Enter on whatever
is sitting there submits text nobody just typed. The fleet used to ship an `input-watchdog` that
did exactly that; it is retired (`attic/retired-tools/`), and the only submit path for stranded
text is a **manual** `fleet-msg kick <ws>` after a human reads the box.

## When the recipient is down

The message is **queued**, never dropped (`~/.agents/messages/pending/<name>.jsonl`), and reported as
queued. `agentctl flush <name>` submits the backlog. A fleet swap calls `flush` automatically after
bringing workspaces back, so a message sent during a bounce lands on resume rather than evaporating.

## The durable log

Delivery is the point; the record is the proof. Every message is written to:

- `~/.agents/messages/log.jsonl` — append-only source of truth
- `~/.agents/messages/messages.db` — SQLite, queryable

Each record carries `msg_id`, timestamp, sender, recipients, the full body, delivery status, and the
**conversation links** — the sender's and recipient's session ids. The `msg_id` is embedded in the
delivered text (`[message from api | msg-…]`), so grepping it in the recipient's transcript finds the
exact turn where it was processed. Bidirectional trace, from either end.

```bash
agentctl msglog --to ws-gpu --since 24h --json | jq '.[] | {ts, from, body}'
```

## System senders and the provenance envelope

Every machine-mediated injection is wrapped in the same envelope the delivered text already
carries:

```
[message from system:<script> | msg-…]
<the text>
```

The rule: **a submission fired by a script must never be indistinguishable from a live human
Enter.** The incident behind it — an automated kicker once submitted a stale draft sitting in a
composer, and the agent executed it with operator authority because it looked exactly like the
operator pressing Enter. The envelope names the *script* (`system:swap-fleet`,
`system:session-guard`), not a bare `system`, so the recipient can weigh the authority correctly,
and the wrapped text lands in the durable log like any message. Wrapping also defuses `!`/`/`
prefixes: enveloped text can no longer execute as a bash or slash command. The shared library's
`fl_send` (see `bin/fleet-lib.sh`) is how every watcher script sends with this identity.

Two ways a script declares itself:

- `FLEET_MSG_FROM="system:<script>"` in its environment — picked up by `agentctl send` /
  `fleet-msg send` (precedence: explicit `--from` > `FLEET_MSG_FROM` > workspace name > `system`).
- `fleet-msg kick <ws> --as <script>` — a kick submits the stranded composer text **enveloped and
  logged** (event `kick` in the durable store), attributed to `system:<script>`.

`kick` is **manual-only** (no automated invoker exists; see the delivery section above for why),
and even a manual kick refuses to submit what isn't operator input:

- **TUI render artifacts** — strings like `No response requested` or `esc to interrupt` that the
  composer reader can misread as stranded input. Cleared, never submitted (UI noise fired as a
  user turn carries user authority; see `_TUI_ARTIFACTS` in `bin/fleet-msg`).
- **Screen-echo of the agent's own output** (`_is_own_echo`) — composer text that appears verbatim
  in the agent's scrollback *above* the composer (excluding past-prompt `❯` lines) is the agent's
  own rendering flushed back at it (stray selection-paste, RC-bridge artifact), not a directive.
  Cleared and logged, never submitted — a live incident fed an agent a fragment of its own
  conversation as if the operator had sent it.

The one standing watcher for stranded text is session-guard's **STUCK INPUT** sweep: detect twice
across 15-minute sweeps, then **page a human** (envelope-tagged `system:session-guard`) with the
`fleet-msg kick` command to run if — and only if — the human decides the text should fire.

## Addressing

| Form | Means |
|---|---|
| `ws-gpu` | one workspace |
| `a,b,c` | several |
| `@all` | every up workspace |
| `@human` | you — routed via the coordinator (`main`) |

## No message types

Messages are **plain text**. There is deliberately no taxonomy — no types, no priorities, no
subjects, no ack/reply protocol. An earlier version had all of that; it added ceremony and zero
capability. Interpretation is the recipient's job, exactly as it is for a message from a person. A
reply is just another `send` back.

## The visibility caveat (worth knowing)

A message delivered while the recipient is **mid-turn** is injected into that turn and surfaced to
the model — but it may not render as a separate, scrollback-visible turn in the pane. The model saw
it; a human scrolling the pane might not. `agentctl msglog` is the reliable record of what was
delivered and when. Don't debug delivery by eyeballing panes.
