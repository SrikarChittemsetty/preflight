# preflight

Approve Claude Code's tool calls by category, in plain English, so a session can run while you're away without stopping at permission popups.

```
You: web searches and editing are fine, never email anyone
     → web + edit run with no popup, email is blocked with no popup,
       anything you didn't approve is blocked and Claude carries on without it
```

Or let Claude ask: "I'm heading out, what will you need?" Claude lists categories and ends with a `PREFLIGHT-REQUEST:` line. You reply "yes" (or "yes but no running code").

Change permissions any time in chat: "stop running code", "you can push for the next hour", "back to normal prompts".

## How it works

| Piece | Hook | Job |
|---|---|---|
| Intent reader | `UserPromptSubmit` | Reads only messages **you** type. Turns permission talk into category approvals (Haiku via `claude -p`, with a keyword fallback). |
| Enforcer | `PreToolUse` | Maps every tool call to categories and answers `allow` or `deny`. Never `ask`, so nothing stalls. |
| Grant store | | `~/.claude/preflight/<session>.json`, expires after 8h by default |
| Log | | `~/.claude/preflight/<session>.log.jsonl` |

Until you approve something in a session, both hooks are silent and Claude Code behaves normally.

Categories: `read` (on by default), `edit`, `run`, `install`, `web`, `git`, `publish`, `message`, `apps`, `delete`. Run `python3 preflight.py categories` to list them.

Always blocked while active: `sudo`, changing permission modes or settings, and editing preflight's own files or state.

## Commands

```bash
python3 preflight.py status   # what's approved right now
python3 preflight.py report   # what ran and what was blocked
python3 -m unittest tests/test_preflight.py
```

## Install

It's a Claude Code plugin. This repo is its own marketplace. Inside Claude Code:

```
/plugin marketplace add SrikarChittemsetty/preflight
/plugin install preflight@preflight
```

Or from a terminal: `claude plugin marketplace add SrikarChittemsetty/preflight && claude plugin install preflight@preflight`.

Requires `python3` (preinstalled on macOS and most Linux) and the `claude` CLI on your PATH for natural-language parsing. Without the CLI it falls back to keyword matching. Remove it with `claude plugin uninstall preflight@preflight`.

## Known limits

- **Approving `run` means arbitrary code.** A script can do anything, including sending email. Only approve `run` when you'd trust the task with it.
- Shell classification is heuristic. Unknown commands count as `run`.
- A time limit ("for the next hour") currently applies to the whole approval set, not just one category.
- Parsing a permission message adds about 4s to that message only.
