def parse(text: str):
    """Return (command, rest) for a slash command, else (None, "")."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, ""
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    if "@" in cmd:  # tolerate /agents@botname
        cmd = cmd.split("@", 1)[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    return cmd, rest
