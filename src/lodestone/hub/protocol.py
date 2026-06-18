"""Checkpoint protocol between Lodestone (supervisor) and remote agents (workers).

The Agent Loop needs to know, after each agent reply, *what just happened* so it
can decide whether to continue, pause for approval, or stop. We support two
parse modes, configurably (see config `loop` / per-agent), tried in order:

1. STRUCTURED — the agent emits a line containing a JSON envelope prefixed with
   ``::LODESTONE::``. This is precise and is what makes gates reliable:

       ::LODESTONE:: {"status":"GATE_PR","seq":3,"summary":"feature ready",
                      "pr_url":"https://github.com/x/y/pull/12","tokens_used":12000}

   Surrounding prose is ignored — we scan for the marker anywhere in the text.

2. HEURISTIC — for agents you cannot modify. We read free-text and guess a
   status: a PR URL => GATE_PR, "done/complete/finished" => DONE, error words =>
   ERROR, otherwise MILESTONE. Less reliable (that is the whole point of the
   structured form), so the supervisor leans on watchdogs when in this mode.

A reply that has no envelope still yields a Checkpoint (heuristic), so a
non-cooperating agent always surfaces — it just lacks structure. The supervisor
never crashes on a malformed reply.
"""

import json
import re
from dataclasses import dataclass, field

MARKER = "::LODESTONE::"

VALID_STATUSES = (
    "MILESTONE",   # progress beat — log, keep going (no LLM tokens)
    "GATE_PR",     # about to create a PR — live projects pause for /approve
    "BLOCKED",     # needs human input — pause and relay your reply back
    "DONE",        # task complete — assemble final delivery
    "BUDGET_WARN", # agent thinks it is nearing budget
    "ERROR",       # agent hit an unrecoverable error
)

_PR_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org)/\S+?/(?:pull|-/merge_requests|pull-requests)/\d+",
    re.IGNORECASE,
)
_DONE_RE = re.compile(r"\b(done|completed?|finished|all set|task complete)\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|failed|failure|exception|traceback|cannot|can't)\b", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"\b(blocked|need(?:s| your)? input|waiting on you|clarif)\b", re.IGNORECASE)


@dataclass
class Checkpoint:
    status: str
    summary: str = ""
    seq: int = None
    pr_url: str = None
    tokens_used: int = 0
    structured: bool = False
    raw: str = ""
    extra: dict = field(default_factory=dict)


def _extract_envelope(text: str):
    """Return the parsed JSON dict after the first MARKER, or None."""
    idx = text.find(MARKER)
    if idx == -1:
        return None
    after = text[idx + len(MARKER):].strip()
    # The JSON object is the marker's payload; take from the first '{' to its
    # matching close. A brace counter tolerates trailing prose on the same line.
    start = after.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(after)):
        c = after[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                blob = after[start:i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_status(value) -> str:
    s = str(value or "").strip().upper()
    return s if s in VALID_STATUSES else "MILESTONE"


def _heuristic(text: str) -> Checkpoint:
    """Best-effort classification of a free-text agent reply."""
    pr = _PR_URL_RE.search(text or "")
    if pr:
        return Checkpoint(status="GATE_PR", summary=_clip(text), pr_url=pr.group(0),
                          structured=False, raw=text)
    if _BLOCKED_RE.search(text or ""):
        return Checkpoint(status="BLOCKED", summary=_clip(text), structured=False, raw=text)
    # ERROR before DONE: "failed to finish" should read as an error, not done.
    if _ERROR_RE.search(text or ""):
        return Checkpoint(status="ERROR", summary=_clip(text), structured=False, raw=text)
    if _DONE_RE.search(text or ""):
        return Checkpoint(status="DONE", summary=_clip(text), structured=False, raw=text)
    return Checkpoint(status="MILESTONE", summary=_clip(text), structured=False, raw=text)


def _clip(text: str, n: int = 500) -> str:
    return (text or "").strip()[:n]


def parse_checkpoint(text: str, allow_heuristic: bool = True) -> Checkpoint:
    """Parse one agent reply into a Checkpoint.

    Tries the structured envelope first. If absent and ``allow_heuristic`` is
    True (configurable per fleet), falls back to free-text classification. With
    heuristics disabled, an envelope-less reply is reported as a MILESTONE
    carrying the raw text (so the run keeps progressing rather than stalling on
    a chatty-but-cooperating agent).
    """
    text = text or ""
    env = _extract_envelope(text)
    if env is not None:
        seq = env.get("seq")
        return Checkpoint(
            status=_normalize_status(env.get("status")),
            summary=_clip(str(env.get("summary", ""))),
            seq=int(seq) if isinstance(seq, (int, float)) else None,
            pr_url=env.get("pr_url") or None,
            tokens_used=int(env.get("tokens_used") or 0),
            structured=True,
            raw=text,
            extra={k: v for k, v in env.items()
                   if k not in ("status", "summary", "seq", "pr_url", "tokens_used")},
        )
    if allow_heuristic:
        return _heuristic(text)
    return Checkpoint(status="MILESTONE", summary=_clip(text), structured=False, raw=text)


def frame_instruction(task: str, project: str, project_status: str,
                      step_budget: int) -> str:
    """The opening instruction the supervisor sends to a remote agent.

    Teaches the agent the checkpoint protocol and the dev/live rule. We cannot
    *force* a remote process to obey this (Lodestone only sends text), so this is
    the best-effort half of the contract; the enforced half is that Lodestone
    withholds the deploy go-ahead for live projects until you /approve.
    """
    live = project_status == "live"
    gate = (
        "This project is LIVE. You MUST stop and report a GATE_PR checkpoint "
        "right after opening the pull request, and then WAIT — do not deploy, "
        "merge, or run post-deploy steps until I reply 'approved'."
        if live else
        "This project is in development. You may proceed through PR creation and "
        "deploy without pausing, but still report each milestone."
    )
    return (
        f"AGENT LOOP — autonomous run for project '{project}'.\n"
        f"Task: {task}\n\n"
        "Work the task to completion in your own internal steps. After each "
        "meaningful step, reply with ONE line in this exact format (prose may "
        "follow on later lines):\n"
        f"  {MARKER} " '{"status":"<STATUS>","seq":<n>,"summary":"<short>",'
        '"pr_url":<url-or-null>,"tokens_used":<int>}\n'
        "STATUS is one of: MILESTONE, GATE_PR, BLOCKED, DONE, BUDGET_WARN, ERROR.\n"
        "seq increases by 1 each checkpoint, starting at 1.\n"
        f"{gate}\n"
        f"Aim to finish within about {step_budget} steps. If you approach your "
        "token budget, send BUDGET_WARN. If you need a decision from me, send "
        "BLOCKED and wait. Send DONE only when the whole task is complete."
    )


CONTINUE = "approved — continue."

