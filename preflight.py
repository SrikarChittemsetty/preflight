#!/usr/bin/env python3
"""Preflight: approve Claude Code tool calls by category, in plain English.

Two hooks do the work:

  hook-prompt  (UserPromptSubmit) reads the messages *you* type and records
               which capability categories you approved or blocked.
  hook-tool    (PreToolUse) answers every tool call from those approvals:
               "allow" (runs, no popup) or "deny" (blocked, no popup, and
               Claude is told why so it can carry on without it).

Until you approve something in a session, both hooks stay silent and Claude
Code behaves exactly as it normally does.

CLI:  preflight.py status | report | categories   [--session ID]
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("PREFLIGHT_STATE_DIR", str(Path.home() / ".claude" / "preflight")))
DEFAULT_HOURS = 8
PARSER_MODEL = os.environ.get("PREFLIGHT_MODEL", "haiku")

CATEGORIES = {
    "read":    "📖 Read files, search code, read data in connected apps",
    "edit":    "✏️ Create and change files",
    "run":     "▶️ Run code, scripts, tests and builds",
    "install": "📦 Install packages",
    "web":     "🌐 Web search, fetch pages, browse",
    "git":     "🔀 Local git: commit, branch, stash",
    "publish": "🚀 Push, deploy, post publicly, send data to servers",
    "message": "✉️ Message people: email, Slack, DMs",
    "apps":    "🧩 Change things in connected apps (drafts, labels, docs)",
    "delete":  "🗑️ Delete files or data",
}
# Reading is on by default once preflight is active, unless the user blocks it.
DEFAULT_ALLOWED = {"read"}

INTERNAL = "internal"    # harmless harness tools: always allowed
FORBIDDEN = "forbidden"  # never allowed while preflight is active

INTERNAL_TOOLS = {
    "ToolSearch", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "TaskOutput", "TaskStop", "Agent", "Task", "Skill", "AskUserQuestion",
    "EnterPlanMode", "ExitPlanMode", "SendMessage", "ListAgents", "ScheduleWakeup",
    "ReportFindings", "PushNotification", "BashOutput", "KillShell",
}
TOOL_CATEGORIES = {
    "Read": "read", "Grep": "read", "Glob": "read", "LS": "read", "NotebookRead": "read",
    "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "WebSearch": "web", "WebFetch": "web",
    "Monitor": "run", "CronCreate": "run", "RemoteTrigger": "run", "Workflow": "run",
    "Artifact": "publish", "ArtifactData": "apps", "ArtifactComments": "apps",
}


# ---------------------------------------------------------------- state

def state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return STATE_DIR / (safe + ".json")


def log_path(session_id: str) -> Path:
    return state_path(session_id).with_suffix(".log.jsonl")


def now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(session_id: str) -> Optional[dict]:
    try:
        return json.loads(state_path(session_id).read_text())
    except (OSError, ValueError):
        return None


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(state["session_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def is_active(state: Optional[dict]) -> bool:
    if not state or state.get("off"):
        return False
    try:
        return datetime.fromisoformat(state["expires_at"]) > now()
    except (KeyError, ValueError):
        return False


def append_log(session_id: str, entry: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = dict(entry, ts=now().isoformat(timespec="seconds"))
    with log_path(session_id).open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------- classification

def classify(tool_name: str, tool_input: dict) -> Set[str]:
    """Return the capability categories a tool call needs."""
    if tool_input.get("dangerouslyDisableSandbox"):
        return {FORBIDDEN}
    if tool_name in INTERNAL_TOOLS:
        return {INTERNAL}
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if is_protected_path(path):
            return {FORBIDDEN}
    if tool_name == "Bash":
        return classify_bash(tool_input.get("command", ""))
    if tool_name in TOOL_CATEGORIES:
        return {TOOL_CATEGORIES[tool_name]}
    if tool_name.startswith("mcp__"):
        return {classify_mcp(tool_name)}
    return {"run"}  # unknown tool: needs an explicit approval


MCP_VERBS = {
    "message": {"send", "reply", "forward", "schedule"},
    "delete": {"delete", "trash", "remove", "purge"},
    "publish": {"publish", "share", "deploy"},
    "read": {"get", "read", "list", "search", "query", "fetch", "download", "find",
             "view", "describe", "export", "show", "check", "status", "me", "guide"},
}


def classify_mcp(tool_name: str) -> str:
    server, _, action = tool_name[len("mcp__"):].partition("__")
    action = action.lower()
    if re.search(r"permission_mode|set_setting", action):
        return FORBIDDEN
    if server in ("Claude_Browser", "claude-in-chrome") or "browser" in server.lower():
        return "web"
    if server.startswith("ccd_"):
        return INTERNAL  # the desktop app's own session/view tools
    if server == "computer-use":
        return "apps"
    for token in action.split("_"):
        for category, verbs in MCP_VERBS.items():
            if token in verbs:
                return category
    return "apps"


READ_CMDS = {
    "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "rg", "fd", "wc",
    "pwd", "echo", "printf", "which", "whoami", "date", "file", "stat", "du", "df",
    "tree", "sort", "uniq", "cut", "awk", "diff", "jq", "env", "printenv", "true",
    "false", "test", "[", "basename", "dirname", "realpath", "readlink", "type",
    "man", "ps", "cd", "sleep", "uname", "hostname", "md5", "shasum", "column", "tr",
}
EDIT_CMDS = {"mkdir", "touch", "cp", "mv", "ln", "tee", "chmod", "chown", "patch", "unzip", "tar"}
DELETE_CMDS = {"rm", "rmdir", "unlink", "trash", "shred"}
REMOTE_CMDS = {"ssh", "scp", "rsync", "sftp"}
MESSAGE_CMDS = {"mail", "mailx", "sendmail", "mutt"}
WRAPPERS = {"nohup", "time", "exec", "command", "nice", "caffeinate"}
PKG_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
INSTALL_VERBS = {"install", "i", "add", "ci", "update", "upgrade"}
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
CLI_READONLY = re.compile(
    r"^\s*python3?\s+\S*preflight\.py\s+(status|report|categories)\b[^;&|<>`$]*$")


def strip_heredocs(cmd: str) -> str:
    """Heredoc bodies are data, not commands."""
    return re.sub(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\b", r"\2", cmd, flags=re.S)


def classify_bash(cmd: str) -> Set[str]:
    if CLI_READONLY.match(cmd):
        return {"read"}
    cmd = strip_heredocs(cmd)
    cats: Set[str] = set()
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", cmd):
        cats |= classify_bash(inner[0] or inner[1])
    try:
        lexer = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return {"run"}
    segment: List[str] = []
    skip_next = False
    for i, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if tok in OPERATORS:
            cats |= classify_segment(segment)
            segment = []
        elif tok in REDIRECTS:
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if target and target != "/dev/null" and not target.startswith("&"):
                cats.add("edit")
            skip_next = True
        elif tok in ("<", "<<", "<<<", ">&", "<&"):
            skip_next = True
        else:
            segment.append(tok)
    cats |= classify_segment(segment)
    if cats - {"read"} and touches_protected(cmd):
        return {FORBIDDEN}
    return (cats - {"read"}) or {"read"}


def classify_segment(words: List[str]) -> Set[str]:
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words = words[1:]  # FOO=bar cmd
    while words and os.path.basename(words[0]) in WRAPPERS:
        words = words[1:]
    if not words:
        return set()
    name = os.path.basename(words[0])
    args = words[1:]
    sub = next((a for a in args if not a.startswith("-")), "")

    if name in ("sudo", "doas", "su"):
        return {FORBIDDEN}
    if name in ("timeout", "xargs", "env") and args:
        rest = [a for a in args if not a.startswith("-") and not re.match(r"^\d+[smhd]?$", a)
                and "=" not in a]
        return classify_segment(rest) if rest else {"read"}
    if name in ("bash", "sh", "zsh") and "-c" in args:
        idx = args.index("-c")
        return classify_bash(args[idx + 1]) if idx + 1 < len(args) else {"run"}
    if name in READ_CMDS:
        return {"read"}
    if name == "sed":
        return {"edit"} if any(a.startswith("-i") for a in args) else {"read"}
    if name == "find":
        if "-delete" in args or ("-exec" in args and any(a in DELETE_CMDS for a in args)):
            return {"delete"}
        return {"read"}
    if name in DELETE_CMDS:
        return {"delete"}
    if name in EDIT_CMDS:
        return {"edit"}
    if name in REMOTE_CMDS:
        return {"publish"}
    if name in MESSAGE_CMDS:
        return {"message"}
    if name == "git":
        return classify_git(args)
    if name == "gh":
        return classify_gh(args)
    if name in ("curl", "wget", "http", "https"):
        sends = any(re.match(r"^(-d|--data.*|-F|--form.*|-T|--upload-file|--json|-XPOST|-XPUT|-XPATCH|-XDELETE)$", a)
                    for a in args)
        sends = sends or any(a == "-X" and args[j + 1].upper() in ("POST", "PUT", "PATCH", "DELETE")
                             for j, a in enumerate(args[:-1]))
        return {"publish"} if sends else {"web"}
    if name in PKG_MANAGERS:
        if sub in INSTALL_VERBS:
            return {"install"}
        if sub == "publish":
            return {"publish"}
        if sub in ("ls", "list", "view", "info", "outdated", "why"):
            return {"read"}
        return {"run"}
    if name in ("npx", "bunx", "pnpx", "uvx"):
        return {"run", "install"}
    if name in ("pip", "pip3", "uv", "poetry", "pipx", "gem", "cargo", "go", "brew"):
        if sub in ("install", "add", "sync", "upgrade", "get", "tap") or args[:2] == ["pip", "install"]:
            return {"install"}
        if sub in ("list", "show", "freeze", "info", "search", "outdated", "doctor"):
            return {"read"}
        if sub == "uninstall":
            return {"delete"}
        if sub == "publish":
            return {"publish"}
        return {"run"}
    if re.match(r"^python[\d.]*$", name) and args[:3] == ["-m", "pip", "install"]:
        return {"install"}
    if name == "docker":
        return {"publish"} if sub == "push" else {"run"}
    if name in ("vercel", "netlify", "firebase", "fly", "flyctl", "twine", "wrangler"):
        return {"publish"}
    if name == "osascript":
        return {"message"} if re.search(r"\bsend\b", " ".join(args), re.I) else {"apps"}
    return {"run"}


def classify_git(args: List[str]) -> Set[str]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c") else 1
    sub = args[i] if i < len(args) else ""
    rest = args[i + 1:]
    if sub in ("status", "log", "diff", "show", "blame", "rev-parse", "ls-files", "grep",
               "describe", "shortlog", "reflog", "config", "remote", ""):
        return {"read"}
    if sub == "branch":
        if any(a in ("-D", "-d", "--delete") for a in rest):
            return {"delete"}
        return {"git"} if any(not a.startswith("-") for a in rest) else {"read"}
    if sub == "push":
        return {"publish"}
    if sub in ("fetch", "pull"):
        return {"web", "git"}
    if sub == "clone":
        return {"web", "edit"}
    if sub == "clean" or (sub == "reset" and "--hard" in rest):
        return {"delete"}
    return {"git"}


def classify_gh(args: List[str]) -> Set[str]:
    words = [a for a in args if not a.startswith("-")]
    pair = " ".join(words[:2])
    if re.match(r"^(pr (create|merge|comment|review|close|edit)|issue (create|comment|close|edit)"
                r"|release (create|upload|delete)|repo (create|delete|edit)|gist create)$", pair):
        return {"publish"}
    if words[:1] == ["api"] and any(a in ("-X", "--method", "-f", "-F", "--input") for a in args):
        return {"publish"}
    return {"web"}


def protected_markers() -> List[str]:
    return [str(STATE_DIR), ".claude/preflight", ".claude/settings", "settings.local.json",
            str(HERE), "preflight.py", "hooks.json", ".claude/plugins"]


def touches_protected(cmd: str) -> bool:
    expanded = cmd.replace("~", str(Path.home()))
    return any(m in expanded for m in protected_markers())


def is_protected_path(path: str) -> bool:
    if not path:
        return False
    p = Path(os.path.expanduser(path)).resolve()
    plugins_dir = Path.home() / ".claude" / "plugins"
    if HERE in p.parents or STATE_DIR in p.parents or plugins_dir in p.parents:
        return True
    return p.parent.name == ".claude" and p.name.startswith("settings")


# ---------------------------------------------------------------- enforcement

def decide(state: dict, cats: Set[str]):
    """Return ("allow" | "deny", missing_categories)."""
    if FORBIDDEN in cats:
        return "deny", {FORBIDDEN}
    if cats <= {INTERNAL}:
        return "allow", set()
    cats = cats - {INTERNAL}
    allowed = (set(state.get("allow", [])) | DEFAULT_ALLOWED) - set(state.get("deny", []))
    missing = cats - allowed
    return ("deny", missing) if missing else ("allow", set())


def deny_reason(missing: Set[str]) -> str:
    if FORBIDDEN in missing:
        return ("Preflight: this action is never allowed while preflight approvals are active "
                "(sudo, changing permission settings, or editing preflight's own files). "
                "Skip it and tell the user at the end.")
    labels = ", ".join(CATEGORIES.get(c, c) for c in sorted(missing))
    keys = ", ".join(sorted(missing))
    return (f"Preflight: this needs {labels}, which the user hasn't approved for this session. "
            "Don't retry it or route around it with another tool or a script that does the same thing. "
            "Continue the rest of the task without it, and at the end tell the user what you skipped "
            f"and that they can approve it by saying e.g. \"allow {keys}\".")


def summarize_input(tool_name: str, tool_input: dict) -> str:
    for key in ("command", "file_path", "url", "query", "pattern", "to", "description"):
        if key in tool_input:
            return str(tool_input[key])[:160]
    return json.dumps(tool_input)[:160]


def hook_tool(payload: dict) -> Optional[dict]:
    session_id = payload.get("session_id", "")
    state = load_state(session_id)
    if not is_active(state):
        return None  # preflight not in use: normal Claude Code behavior
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cats = classify(tool_name, tool_input)
    decision, missing = decide(state, cats)
    reason = deny_reason(missing) if decision == "deny" else "Pre-approved via preflight"
    if cats != {INTERNAL}:
        append_log(session_id, {"tool": tool_name, "input": summarize_input(tool_name, tool_input),
                                "categories": sorted(cats), "decision": decision,
                                "missing": sorted(missing)})
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}


# ---------------------------------------------------------------- understanding the user

REQUEST_LINE = re.compile(r"PREFLIGHT-REQUEST:\s*(.+)", re.I)
AFFIRMATIVE = re.compile(
    r"^\s*(y|yes|yeah|yep|yup|sure|ok|okay|approved?|go ahead|go for it|sounds good|do it|lgtm"
    r"|that'?s fine|all good)\b[\s.!]*$", re.I)
PERMISSION_WORDS = re.compile(
    r"\b(allow|approve|pre-?approve|permission|permit|go ahead|fine|ok|okay|don'?t|do not|never"
    r"|stop|block|revoke|forbid|not allowed|can'?t|cannot|no more|yes|yeah|yep|sure|preflight"
    r"|allowed to|heading out|leaving|while i'?m (gone|away|out))\b", re.I)
CATEGORY_WORDS = {
    "web": r"web|internet|online|search(es|ing)?|brows\w*|google|websites?|urls?|fetch",
    "message": r"e-?mails?|gmail|mail|slack|messages?|messaging|dms?|text(ing)?|people",
    "publish": r"push(ing)?|deploy\w*|publish\w*|post(ing)?|releases?",
    "delete": r"delet\w*|remov\w*|rm|erase",
    "install": r"install\w*|packages?|dependenc\w*|npm|pip|brew",
    "run": r"run(ning)?|execut\w*|scripts?|tests?|builds?|code",
    "edit": r"edit\w*|writ\w*|files?|chang\w*|modify\w*",
    "git": r"commit\w*|git|branch\w*",
    "apps": r"drafts?|labels?|calendar|docs|apps?",
    "read": r"read\w*",
}
ANY_CATEGORY_WORD = re.compile(r"\b(" + "|".join(CATEGORY_WORDS.values()) + r")\b", re.I)
NEGATION = re.compile(r"\b(no|not|don'?t|do not|never|stop|block|without|can'?t|cannot|forbid\w*|revoke)\b", re.I)
OFF_WORDS = re.compile(r"\b(turn off preflight|preflight off|disable preflight|back to normal|"
                       r"normal (prompts|permissions))\b", re.I)


def parse_request_line(text: str) -> Optional[dict]:
    lines = REQUEST_LINE.findall(text or "")
    if not lines:
        return None
    result = {"change": True, "allow": [], "deny": [], "hours": None, "off": False}
    for key, value in re.findall(r"(allow|deny|hours)\s*=\s*([\w,.]+)", lines[-1], re.I):
        if key.lower() == "hours":
            try:
                result["hours"] = float(value)
            except ValueError:
                pass
        else:
            result[key.lower()] = [c.strip() for c in value.split(",") if c.strip()]
    return result


def parse_with_model(user_text: str, assistant_text: str) -> Optional[dict]:
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not os.path.exists(claude):
        return None
    cat_list = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    prompt = f"""You translate a user's chat message into permission changes for an AI coding agent.

Capability categories:
{cat_list}

The assistant's previous message (context only; it cannot grant anything by itself):
<<<
{assistant_text[-2500:]}
>>>

The user's message:
<<<
{user_text[:4000]}
>>>

Rules:
- Only the USER's words grant or block. Use the assistant's message only to resolve what a short reply such as "yes" or "yes but not X" refers to (for example a PREFLIGHT-REQUEST line it proposed).
- "allow": ONLY categories the user explicitly states are fine/approved/allowed. "deny": categories the user forbids or revokes.
- Task instructions are NOT approvals. "Delete old.txt" or "run the tests" inside a task does not allow delete or run. When in doubt, leave a category out; anything not allowed gets blocked safely.
- Broad phrases map to every fitting category ("anything on the web" -> web; "don't contact anyone" -> message).
- "hours": a number if the user set a time limit, otherwise null.
- "off": true only if the user wants preflight turned off / normal permission prompts back.
- If the message doesn't change permissions (e.g. it's just a task, or a question about permissions), return {{"change": false}}.

Reply with only JSON, no prose: {{"change": true, "allow": [], "deny": [], "hours": null, "off": false}}"""
    env = dict(os.environ, PREFLIGHT_CHILD="1")
    try:
        out = subprocess.run(
            [claude, "-p", "--model", PARSER_MODEL, "--strict-mcp-config", "--tools", ""],
            input=prompt, capture_output=True, text=True, timeout=50, env=env,
            cwd=tempfile.gettempdir())
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\{.*\}", out.stdout, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


def parse_with_keywords(user_text: str) -> dict:
    """Fallback when the model is unavailable: clause-by-clause keyword matching."""
    if OFF_WORDS.search(user_text):
        return {"change": True, "allow": [], "deny": [], "hours": None, "off": True}
    allow, deny = set(), set()
    for clause in re.split(r"[,.;!?\n]|\bbut\b|\band\b|\bexcept\b", user_text, flags=re.I):
        for cat, words in CATEGORY_WORDS.items():
            if re.search(r"\b(" + words + r")\b", clause, re.I):
                (deny if NEGATION.search(clause) else allow).add(cat)
    allow -= deny
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b", user_text, re.I)
    if re.search(r"\b(an|one|the next) hour\b", user_text, re.I) and not hours:
        hours_val = 1.0
    else:
        hours_val = float(hours.group(1)) if hours else None
    return {"change": bool(allow or deny), "allow": sorted(allow), "deny": sorted(deny),
            "hours": hours_val, "off": False}


def last_assistant_text(transcript_path: str) -> str:
    try:
        lines = Path(transcript_path).read_text().splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    return ""


def understand(user_text: str, assistant_text: str) -> Optional[dict]:
    """Work out what permission change, if any, the user's message makes."""
    if AFFIRMATIVE.match(user_text):
        proposal = parse_request_line(assistant_text)
        if proposal:
            return proposal
    if not (OFF_WORDS.search(user_text)
            or (PERMISSION_WORDS.search(user_text) and ANY_CATEGORY_WORD.search(user_text))
            or (PERMISSION_WORDS.search(user_text) and REQUEST_LINE.search(assistant_text))):
        return None  # not about permissions; don't spend a model call on it
    parsed = parse_with_model(user_text, assistant_text)
    if parsed is None:
        parsed = parse_with_keywords(user_text)
    return parsed if parsed.get("change") else None


def apply_change(state: Optional[dict], session_id: str, change: dict, user_text: str) -> dict:
    if not state or not is_active(state):
        state = {"session_id": session_id, "created_at": now().isoformat(timespec="seconds"),
                 "allow": [], "deny": [], "history": []}
    state["off"] = bool(change.get("off"))
    allow = set(state.get("allow", []))
    deny = set(state.get("deny", []))
    for cat in change.get("allow") or []:
        if cat in CATEGORIES:
            allow.add(cat)
            deny.discard(cat)
    for cat in change.get("deny") or []:
        if cat in CATEGORIES:
            deny.add(cat)
            allow.discard(cat)
    state["allow"], state["deny"] = sorted(allow), sorted(deny)
    hours = change.get("hours") or DEFAULT_HOURS
    if change.get("hours") or "expires_at" not in state:
        state["expires_at"] = (now() + timedelta(hours=float(hours))).isoformat(timespec="seconds")
    state.setdefault("history", []).append({
        "ts": now().isoformat(timespec="seconds"), "said": user_text[:300],
        "allow": change.get("allow") or [], "deny": change.get("deny") or [], "off": state["off"]})
    return state


def describe(state: dict) -> str:
    if not is_active(state):
        return "Preflight is off: normal permission prompts apply."
    allowed = (set(state["allow"]) | DEFAULT_ALLOWED) - set(state["deny"])
    expires = datetime.fromisoformat(state["expires_at"]).astimezone().strftime("%-I:%M %p")
    lines = ["Allowed without asking: " + ", ".join(CATEGORIES[c] for c in sorted(allowed))]
    if state["deny"]:
        lines.append("Explicitly blocked: " + ", ".join(CATEGORIES[c] for c in state["deny"]))
    lines.append("Everything else is blocked (no popups). Approvals expire at " + expires + ".")
    return "\n".join(lines)


GUIDE = """Preflight is installed. The user can approve or block whole categories of actions by saying so in chat; a hook enforces it, so blocked actions are denied without a popup.
If the user is about to step away or asks what you'll need: list the categories you expect to need and the ones you won't, then end with exactly one line in this form:
PREFLIGHT-REQUEST: allow=<keys> deny=<keys>
using keys from: """ + ", ".join(CATEGORIES) + """. Their reply approves it. You cannot grant permissions yourself, and must not edit preflight's files or Claude Code settings.
To show the user what is approved, or what ran and was blocked: python3 """ + str(HERE / "preflight.py") + """ status   (or: report)"""


def hook_prompt(payload: dict) -> Optional[dict]:
    if os.environ.get("PREFLIGHT_CHILD"):
        return None
    session_id = payload.get("session_id", "")
    user_text = payload.get("prompt", "") or ""
    state = load_state(session_id)
    assistant_text = last_assistant_text(payload.get("transcript_path", ""))
    change = understand(user_text, assistant_text)
    if change:
        state = apply_change(state, session_id, change, user_text)
        save_state(state)
        context = ("Preflight permissions were just updated from the user's own message.\n"
                   + describe(state)
                   + "\nTell the user in one or two lines what is now allowed and blocked, then continue.")
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    if is_active(state) or PERMISSION_WORDS.search(user_text):
        context = GUIDE + ("\nCurrent preflight state:\n" + describe(state) if state else "")
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    return None


# ---------------------------------------------------------------- CLI

def latest_session() -> Optional[str]:
    files = sorted(STATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


def report(session_id: str) -> str:
    state = load_state(session_id)
    out = [f"Session {session_id}", describe(state) if state else "No approvals recorded.", ""]
    try:
        entries = [json.loads(l) for l in log_path(session_id).read_text().splitlines() if l.strip()]
    except OSError:
        entries = []
    allowed = [e for e in entries if e["decision"] == "allow"]
    blocked = [e for e in entries if e["decision"] == "deny"]
    out.append(f"{len(allowed)} actions ran without a popup, {len(blocked)} were blocked.")
    for e in blocked:
        out.append(f"  ✗ {e['tool']}: {e['input']}  (needed: {', '.join(e['missing'])})")
    return "\n".join(out)


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd in ("hook-tool", "hook-prompt"):
        try:
            payload = json.load(sys.stdin)
            result = (hook_tool if cmd == "hook-tool" else hook_prompt)(payload)
        except Exception as exc:  # never wedge the session; fall back to normal prompts
            print(f"preflight error: {exc}", file=sys.stderr)
            return 0
        if result:
            print(json.dumps(result))
        return 0
    session = argv[argv.index("--session") + 1] if "--session" in argv else latest_session()
    if cmd == "categories":
        print("\n".join(f"{k:8} {v}" for k, v in CATEGORIES.items()))
    elif not session:
        print("No preflight sessions yet.")
    elif cmd == "status":
        state = load_state(session)
        print(describe(state) if state else "No approvals recorded.")
    elif cmd == "report":
        print(report(session))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
