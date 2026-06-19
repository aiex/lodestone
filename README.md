# Lodestone

A single-channel **control plane** for a fleet of Telegram agents.

You run several agents (bots) that each own different projects and live on
different servers with different permissions. As the fleet grows you lose the
overview — *which agent owns what, and what is it allowed to touch?* — and you
end up scrolling old chats to remember. Lodestone gives you **one channel** to
see the whole fleet and to dispatch work to any agent.

> Project name: **Lodestone** · Package name: **lodestone-hub**

## How it works

Telegram bots **cannot read each other's messages** (a platform rule), so a bot
cannot orchestrate other bots. Lodestone runs as a **userbot** — a normal
Telegram *account* (via MTProto/Telethon) — which *can* message your agent bots
and read their replies. You talk to one "hub" group; the userbot routes your
commands to the right agent and reports back.

```
you ──> hub group ──> Lodestone (userbot) ──> agent bots
                          │
                          └── registry (SQLite): agents / projects / perms / logs
```

`config.yaml` is the source of truth for your fleet structure. It is synced into
a local SQLite registry on every start; the `logs` and `ai_usage` tables are
append-only history — what the [dashboard](#the-dashboard) reads from.

If you enable the optional TencentDB-Agent-Memory Gateway, Lodestone also gains
a **shared memory plane**: the orchestrator can recall compact fleet context
before routing work, and agent interactions are captured out-of-band so context
does not have to stay in the prompt forever.

## Privacy model — open source without leaking anything

The repo ships only **code + a placeholder template**. All real fleet info stays
local and is gitignored from the first commit:

- committed: code, `config.example.yaml`, schema, docs
- **never committed** (`.gitignore`): `config/`, `data/`, `*.session`, `.env`

Bot tokens, chat ids, server hosts, agent names, project names and permission
maps all live in `config/config.yaml` and the SQLite file — never in source.

## Requirements

- Python 3.10+
- A Telegram `api_id` / `api_hash` from <https://my.telegram.org> → *API
  development tools*
- A Telegram account to run the orchestrator. **A dedicated account is
  recommended** — if its session leaks, only this tooling account is exposed,
  not your personal identity.

## Setup

```bash
git clone <your-repo-url> lodestone && cd lodestone
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt

mkdir -p config data
cp config.example.yaml config/config.yaml
# edit config/config.yaml: api_id, api_hash, your agents
```

1. Add the orchestrator account to each conversation with your agent bots, and
   create a private group to act as your **hub channel**; add the account to it.
2. Find the hub group id:
   ```bash
   lodestone chats      # first run prompts for phone + login code
   ```
   Put that id into `telegram.hub_chat_id` in `config/config.yaml`.
   Hub-group control also uses `telegram.allowed_users`; if the allow-list is
   empty, Lodestone now fails safe and disables control instead of responding.
3. Initialize, log in the account once, then run:
   ```bash
   lodestone init
   lodestone login    # one-time: enter phone + code to create the session
   lodestone run      # starts account + front-door bot + AI brain
   ```
4. DM your front-door bot (or send `/agents` in the hub group) to verify.

## Talking to Lodestone

Two ways in, sharing one brain:

- **Front-door bot** (recommended): DM your BotFather bot. Slash commands work,
  and plain language is interpreted by the AI brain, which finds the right agent
  and dispatches for you. Only `allowed_users` may command it.
- **Hub group fallback:** if you do not set `bot_token`, the orchestrator
  account listens in `hub_chat_id` instead. This path uses the same
  `allowed_users` allow-list; if either `hub_chat_id` or `allowed_users` is
  missing, hub control stays disabled.
- **Slash commands** (deterministic), on the bot or the hub group:

| Command | What it does |
| --- | --- |
| `/agents` | List every agent with its projects + permission summary |
| `/agent <id>` | Full detail for one agent + recent activity |
| `/project <name>` | Look up which agent owns one project |
| `/projects` | Reverse lookup: project → which agent runs it |
| `/memory_status` | Check whether the memory gateway is enabled and healthy |
| `/memory_search <agent_id> <query>` | Search one agent's structured memory |
| `/memory_search_project <project> <query>` | Search memory scoped to a project's owning agent |
| `/dispatch <agent_id> <task>` | Send a task to an agent, wait, report the reply |
| `/dispatch_project <project> <task>` | Validate the project's owner from the registry, then dispatch |
| `/loop <project> <task>` | Estimate an autonomous [Agent Loop](#the-agent-loop) (then confirm) |
| `/loop_confirm <task_id>` | Start the estimated loop |
| `/loop_status [task_id]` | Show running loops + budget consumption |
| `/loop_input <task_id> <text>` | Answer a loop that reported `BLOCKED` |
| `/approve <task_id>` / `/reject <task_id>` | Approve/reject a live project's PR gate |
| `/loop_stop <task_id>` | Stop a running loop |
| `/help` | Show command help |

Natural language (e.g. "tell the cricket agent to refresh today's data") goes to
the AI brain when `ai.api_key` is set; otherwise only slash commands work.

## Unified Memory

Lodestone can plug into
[`TencentDB-Agent-Memory`](https://github.com/TencentCloud/TencentDB-Agent-Memory)
through its local HTTP Gateway. This gives you one place to manage per-agent and
orchestrator memory without stuffing long chat history back into every prompt.

- **Recall before routing.** The orchestrator prefetches compact memory context
  before planning, and the model can use `search_agent_memory` /
  `search_agent_conversation` tools to inspect a candidate agent's prior work.
- **Capture after work.** Successful dispatches and meaningful Agent Loop
  exchanges are written back to the Gateway, so each agent's operational memory
  accumulates outside Telegram chat scrollback.
- **Token discipline by design.** Lodestone only injects recalled snippets; the
  heavy L0-L3 layering, summarization, and storage lifecycle stay on the memory
  Gateway side.

Minimal config:

```yaml
memory:
  enabled: true
  base_url: "http://127.0.0.1:8420"
  api_key: ""          # set if you enabled Gateway auth
  namespace: "lodestone"
  timeout: 10
```

The default `base_url` matches TencentDB-Agent-Memory's standalone Gateway. If
you enable the Gateway's auth switch, set `memory.api_key` so Lodestone sends a
Bearer token. When a user request names a known project, Lodestone now performs
an extra **project-scoped recall** against that owning agent's memory stream, so
routing can use prior project context instead of only fleet-global memory.

Operational checks:

```bash
lodestone memory-status
lodestone memory-smoke
```

`memory-status` does a lightweight `/health` probe; `memory-smoke` runs a small
recall + session-end round trip so you can validate that the Gateway is not just
up, but actually serving memory requests.

## The dashboard

A read-only web view over the same registry — agents, projects, permissions,
recent activity, and AI **usage/cost** charts.

```bash
lodestone dashboard      # prints a URL with a token, e.g.
#   http://127.0.0.1:8765/?token=…
```

- **Localhost + token auth.** It binds to `127.0.0.1` by default and every
  request must carry the token (`?token=…` on first visit sets a cookie; API
  clients can send `Authorization: Bearer …` or `X-Lodestone-Token`). Set a
  fixed `web.token` in config for a stable bookmark, or leave it blank to get a
  fresh token each start. Don't move `web.host` off loopback without putting
  your own auth/TLS in front.
- **Read-only.** The dashboard never mutates the fleet — `config.yaml` stays the
  source of truth. It only reads the SQLite registry.
- **Charts are views over a stable schema.** They read the append-only `logs`
  and `ai_usage` tables, so adding a chart is additive — no schema change.

### Usage & cost tracking

When the AI brain is on, every LLM call's token counts are recorded to the
`ai_usage` table, and cost is computed at write time from per-model pricing
(`ai.pricing`, with sensible defaults for common models). The dashboard shows
lifetime totals, daily activity/cost/token charts, per-model and per-agent AI
usage breakdowns, recent activity, and recent AI calls. Token counts are always
exact; only the cost column depends on the pricing you configure.

JSON endpoints (same token) back every panel, if you want to script against them:
`/api/stats`, `/api/agents`, `/api/projects`, `/api/activity`, `/api/usage`,
`/api/memory`.

The memory panel now shows:

- backend health / base_url / namespace
- recent memory errors
- daily memory operations
- memory activity by kind / agent / project
- recent memory events

## The Agent Loop

A business task often needs many agent steps. The Agent Loop drives one to
completion without you babysitting each step — within a token budget, and with
Lodestone keeping supervisory control.

**The model is supervisor/worker.** Lodestone is the supervisor: it owns
*whether* work continues, the dev/live gate, and the budget. The remote agent is
the worker: it self-loops through its own cheap internal steps and reports a
*checkpoint* after each one. Routine progress costs **zero** orchestrator tokens
— Lodestone only parses checkpoints and decides continue / pause / stop.

```bash
/loop coldplay.io "add the playlist export feature"
#  -> ~30,000 tokens estimated (… x 2 safety); budget 2,000,000 -> fits.
#     Task id: ab12cd34ef56   Start it with: /loop_confirm ab12cd34ef56
/loop_confirm ab12cd34ef56
```

- **Estimate before work.** `/loop` does not start anything — it runs a cheap
  estimate (step count × historical average tokens/call × a safety band),
  compares it to the configured `loop.token_budget`, and waits for your
  `/loop_confirm`. If a run later risks exceeding budget, you get an alert with
  an updated projection; the hard cap stops it at 100%.
- **Dev vs live gate.** A project's `status` (in `config.yaml`, `dev` by
  default) decides the gate. **Dev** projects run straight through PR creation
  and deploy. **Live** projects HALT at PR creation: Lodestone reports the PR and
  waits — only your `/approve <task_id>` unblocks deploy/test/delivery
  (`/reject` stops the run). This is enforced on Lodestone's side: it withholds
  the go-ahead until you approve.
- **Checkpoint protocol (configurable).** Cooperating agents emit a structured
  `::LODESTONE:: {…}` envelope (precise gates). Agents you can't modify are read
  heuristically from free text (a PR URL still trips the live gate). Watchdogs —
  a step cap and a heartbeat timeout — cover an agent that ignores the protocol
  or loops forever.

```yaml
loop:
  enabled: true
  token_budget: 2000000     # per-run hard cap
  max_steps: 40             # loop-forever guard
  heartbeat_timeout: 300    # seconds of silence -> stalled
  warn_at: 0.75             # budget alert thresholds
  constrain_at: 0.90

agents:
  - id: openclaw-1
    projects:
      - name: coldplay.io
        status: live          # halts at PR for /approve
      - cricap                 # bare string => status: dev
```

> **Limit:** Lodestone reaches agents only over Telegram text, so the remote
> self-loop's PR halt is best-effort (it depends on the agent honoring the
> protocol), backed by watchdogs. Lodestone *does* guarantee the deploy gate by
> withholding the go-ahead. A remote agent coded to deploy entirely on its own,
> ignoring Lodestone, can't be stopped by a message-only orchestrator — wrapping
> the agent's own PR/deploy tool on its host is the follow-up for that.

## The AI brain

One OpenAI-compatible adapter covers OpenAI, Kimi, GLM, or a Meridian proxy —
set `ai.base_url` / `ai.api_key` / `ai.model`. The brain is given deterministic
tools (`list_agents`, `get_agent`, `get_project_owner`, `dispatch`,
`dispatch_project`, `search_agent_memory`, `search_agent_conversation`,
`start_loop`, `loop_status`) — the same operations behind the slash commands plus
memory lookup. When a request is about a known project, it should prefer
`dispatch_project`, which validates routing against the registry before messaging
an agent. When prior operational context matters, it can inspect the target
agent's memory before deciding. `start_loop` only *estimates* an
[Agent Loop](#the-agent-loop) and returns a task id — the human confirm and the
live-project PR gate are enforced in code, never at the model's discretion.
Note: a Claude *API key* is not your Claude Max subscription; point `base_url`
at a proxy if you want to reuse a subscription.

## Account modes

- **Dedicated account (recommended):** issue commands from your *personal*
  account in the hub group; the orchestrator account sees them as incoming.
- **Single account:** the same account runs the userbot and types commands.
  This works too — only slash-commands are handled, so the bot's own replies
  never re-trigger it.

## Roadmap

- **Phase 1 (done):** registry + CLI + userbot dispatch (deterministic tools).
- **Phase 2 (done):** multi-provider AI brain (function-calling over the tools)
  + front-door Telegram bot for natural-language control.
- **Phase 3 (done):** web dashboard (FastAPI + token-on-localhost auth + charts)
  reading the registry, plus usage/cost tracking. Charts are views over a stable
  schema, so extending them is additive. See [The dashboard](#the-dashboard).
- **Phase 4 (done):** Agent Loop — supervised autonomous runs with a token
  budget and a dev/live PR-approval gate. See [The Agent Loop](#the-agent-loop).

## License

MIT.
