import re

_INLINE_RE = re.compile(r"^\s*\[requires:\s*([^\]]*)\]\s*", re.IGNORECASE)
_STORED_RE = re.compile(r"^\s*\[\[lodestone-perms:\s*([^\]]*)\]\]\s*", re.IGNORECASE)


def _dedupe(values):
    out = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_scopes(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = []
        for item in raw:
            parts.extend(str(item).split(","))
    return _dedupe([part.strip() for part in parts if str(part).strip()])


def extract_task_requirements(task: str, required_permissions=None) -> tuple[str, list]:
    text = (task or "").strip()
    found = []
    for pattern in (_STORED_RE, _INLINE_RE):
        match = pattern.match(text)
        if match:
            found.extend(parse_scopes(match.group(1)))
            text = text[match.end():].lstrip()
    found.extend(parse_scopes(required_permissions))
    return text, _dedupe(found)


def embed_task_requirements(task: str, required_permissions) -> str:
    scopes = parse_scopes(required_permissions)
    if not scopes:
        return task
    return f"[[lodestone-perms:{','.join(scopes)}]]\n{task}"


def grants_scope(granted: str, required: str) -> bool:
    granted = (granted or "").strip()
    required = (required or "").strip()
    if not granted or not required:
        return False
    if granted == "*" or granted == required:
        return True
    if granted.endswith("*"):
        return required.startswith(granted[:-1])
    return False


def missing_permissions(agent: dict, required_permissions) -> list:
    required = parse_scopes(required_permissions)
    granted = parse_scopes((agent or {}).get("permissions", []))
    return [scope for scope in required if not any(grants_scope(g, scope) for g in granted)]


def permission_denied_message(agent_id: str, agent: dict, missing: list) -> str:
    have = ", ".join(parse_scopes((agent or {}).get("permissions", []))) or "—"
    return (
        f"{agent_id} lacks required permissions: {', '.join(missing)} "
        f"(has: {have})"
    )


def annotate_detail(task: str, project_name: str = None, required_permissions=None) -> str:
    parts = []
    if project_name:
        parts.append(f"[project:{project_name}]")
    scopes = parse_scopes(required_permissions)
    if scopes:
        parts.append(f"[requires:{','.join(scopes)}]")
    parts.append(task)
    return " ".join(part for part in parts if part).strip()
