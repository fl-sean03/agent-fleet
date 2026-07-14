# Inter-agent messaging

```bash
agentctl send ws-gpu "profile the matmul kernel; report the top 3 hotspots"
agentctl send a,b,c "standup: one line each"
agentctl send @all "pausing for a deploy — checkpoint and idle"
agentctl msglog 20 --from main --since 2h
agentctl flush ws-gpu            # redeliver what queued while it was down
agentctl kick ws-gpu             # submit input that got stranded in its prompt
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

## How delivery actually works

The mechanics are small but every part is load-bearing:

1. **Target the agent's pane, not the session.** A workspace's tmux session has a control shell in
   pane 1 and the agent in pane 2. Sending to "the session" hits whatever pane is active — usually
   the shell, where your message becomes a shell command nobody ever sees. Deliver to pane 2.
2. **Bracketed paste, then wait, then Enter.** This is the whole trick:

   ```
   tmux load-buffer  →  paste-buffer -p  →  sleep ~0.8s  →  send-keys Enter
   ```

   An `Enter` fired before the bracketed paste settles is **swallowed**, and the text sits in the
   prompt forever, unsubmitted. That single missing delay was the cause of every "stranded input"
   bug in this system's history. (`agentctl kick` exists to rescue agents stranded by *other* means:
   clear the prompt, re-paste, submit.)
3. **Busy agents are fine.** A recipient mid-turn has the pasted text queued by its harness and
   surfaced to the model as a mid-turn message. You do not need to wait for idle.

Everything else people assume is necessary — waiting for idle, clearing the input first, retry
loops — was tried, tested, and **removed**. The delay is the mechanism.

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
