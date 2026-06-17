import argparse

from .config import load_config
from .registry import db


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lodestone", description="Lodestone — agent fleet control plane"
    )
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("init", help="create the database and sync from config")
    sub.add_parser("sync", help="re-sync agents/projects/permissions from config")
    sub.add_parser("run", help="start the hub (account + front-door bot + AI brain)")
    sub.add_parser("login", help="one-time interactive login to create the account session")
    sub.add_parser("agents", help="print agents to the terminal (no Telegram needed)")
    sub.add_parser("chats", help="list recent chats and their ids (to find hub_chat_id)")
    args = parser.parse_args()

    if args.cmd == "init":
        config = load_config(args.config)
        db.init_db(config.db_path)
        db.sync_from_config(config.db_path, config)
        print(f"OK: database ready and synced -> {config.db_path}")

    elif args.cmd == "sync":
        config = load_config(args.config)
        db.sync_from_config(config.db_path, config)
        print("OK: synced from config.")

    elif args.cmd == "agents":
        from .hub import commands
        config = load_config(args.config)
        db.init_db(config.db_path)
        db.sync_from_config(config.db_path, config)
        print(commands.cmd_agents(config.db_path))

    elif args.cmd == "chats":
        _list_chats(args.config)

    elif args.cmd == "login":
        _login(args.config)

    elif args.cmd == "run":
        from .hub import runner
        runner.run(args.config)

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


if __name__ == "__main__":
    main()
