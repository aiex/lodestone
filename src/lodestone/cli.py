import argparse
import asyncio

from .config import load_config
from .memory import build_memory_client
from .registry import db


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="path to config.yaml")
    parser = argparse.ArgumentParser(
        parents=[common],
        prog="lodestone", description="Lodestone — agent fleet control plane"
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("init", parents=[common], help="create the database and sync from config")
    sub.add_parser("sync", parents=[common], help="re-sync agents/projects/permissions from config")
    sub.add_parser("run", parents=[common], help="start the hub (account + front-door bot + AI brain)")
    sub.add_parser("dashboard", parents=[common], help="serve the web dashboard (FastAPI, localhost + token)")
    sub.add_parser("login", parents=[common], help="one-time interactive login to create the account session")
    sub.add_parser("agents", parents=[common], help="print agents to the terminal (no Telegram needed)")
    sub.add_parser("chats", parents=[common], help="list recent chats and their ids (to find hub_chat_id)")
    sub.add_parser("memory-status", parents=[common], help="check the TencentDB-Agent-Memory Gateway health")
    sub.add_parser("memory-smoke", parents=[common], help="run a minimal end-to-end memory smoke test")
    args = parser.parse_args()
    config_path = getattr(args, "config", None)

    if args.cmd == "init":
        config = load_config(config_path)
        db.init_db(config.db_path)
        db.sync_from_config(config.db_path, config)
        print(f"OK: database ready and synced -> {config.db_path}")

    elif args.cmd == "sync":
        config = load_config(config_path)
        db.sync_from_config(config.db_path, config)
        print("OK: synced from config.")

    elif args.cmd == "agents":
        from .hub import commands
        config = load_config(config_path)
        db.init_db(config.db_path)
        db.sync_from_config(config.db_path, config)
        print(commands.cmd_agents(config.db_path))

    elif args.cmd == "chats":
        _list_chats(config_path)

    elif args.cmd == "login":
        _login(config_path)

    elif args.cmd == "run":
        from .hub import runner
        runner.run(config_path)

    elif args.cmd == "dashboard":
        from .web import app as webapp
        webapp.serve(config_path)

    elif args.cmd == "memory-status":
        _memory_status(config_path)

    elif args.cmd == "memory-smoke":
        _memory_smoke(config_path)

    else:
        parser.print_help()


def _list_chats(config_path) -> None:
    from telethon import TelegramClient
    config = load_config(config_path)
    tg = config.telegram
    client = TelegramClient(
        tg.get("session", "data/lodestone.session"),
        int(tg["api_id"]),
        tg["api_hash"],
    )

    async def _go():
        print("chat_id\tname")
        async for d in client.iter_dialogs(limit=30):
            print(f"{d.id}\t{d.name}")

    with client:
        client.loop.run_until_complete(_go())


def _login(config_path) -> None:
    from telethon import TelegramClient
    config = load_config(config_path)
    tg = config.telegram
    client = TelegramClient(
        tg.get("session", "data/lodestone.session"),
        int(tg["api_id"]),
        tg["api_hash"],
    )
    with client:  # prompts for phone number + login code on first run
        me = client.loop.run_until_complete(client.get_me())
        print(f"Logged in as {me.first_name} (id={me.id}). Session saved.")


def _build_memory(config_path):
    config = load_config(config_path)
    memory = build_memory_client(config)
    return config, memory


def _print_memory_status(status: dict) -> None:
    print(f"configured: {'yes' if status.get('configured') else 'no'}")
    if status.get("base_url"):
        print(f"base_url:   {status['base_url']}")
    if status.get("namespace"):
        print(f"namespace:  {status['namespace']}")
    print(f"healthy:    {'yes' if status.get('ok') else 'no'}")
    if "smoke_ok" in status:
        print(f"smoke_ok:   {'yes' if status.get('smoke_ok') else 'no'}")
    print(f"detail:     {status.get('detail', '')}")


def _memory_status(config_path) -> None:
    _config, memory = _build_memory(config_path)
    if memory is None:
        _print_memory_status({"configured": False, "ok": False, "detail": "memory is disabled in config"})
        return
    _print_memory_status(asyncio.run(memory.health()))


def _memory_smoke(config_path) -> None:
    _config, memory = _build_memory(config_path)
    if memory is None:
        _print_memory_status({"configured": False, "ok": False, "detail": "memory is disabled in config"})
        return
    _print_memory_status(asyncio.run(memory.smoke_test()))


if __name__ == "__main__":
    main()
