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
a local SQLite registry on every start; the `logs` table is append-only history
(and what the future dashboard will read from).

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
- **Slash commands** (deterministic), on the bot or the hub group:

| Command | What it does |
| --- | --- |
| `/agents` | List every agent with its projects + permission summary |
| `/agent <id>` | Full detail for one agent + recent activity |
| `/projects` | Reverse lookup: project → which agent runs it |
| `/dispatch <agent_id> <task>` | Send a task to an agent, wait, report the reply |
| `/help` | Show command help |

Natural language (e.g. "tell the cricket agent to refresh today's data") goes to
the AI brain when `ai.api_key` is set; otherwise only slash commands work.

## The AI brain

One OpenAI-compatible adapter covers OpenAI, Kimi, GLM, or a Meridian proxy —
set `ai.base_url` / `ai.api_key` / `ai.model`. The brain is given three tools
(`list_agents`, `get_agent`, `dispatch`) — the same operations behind the slash
commands — and uses function-calling to look up and route work. Note: a Claude
*API key* is not your Claude Max subscription; point `base_url` at a proxy if you
want to reuse a subscription.

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
- **Phase 3 (next):** web dashboard (FastAPI + token-on-localhost auth + charts)
  reading the registry, plus usage/cost tracking. Charts are views over a stable
  schema, so extending them is additive.

## License

MIT.
