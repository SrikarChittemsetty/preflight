# preflight

Approve Claude Code's tool calls by category, in plain English, so a session can run while you're away without stopping at permission popups.

```
You: web searches and editing are fine, never email anyone
     → web + edit run with no popup, email is blocked with no popup,
       anything you didn't approve is blocked and Claude carries on without it
```

Grant everything at once with "use any tools you need", or let Claude ask: "I'm heading out, what will you need?" Claude lists categories and ends with a `PREFLIGHT-REQUEST:` line. You reply "yes" (or "yes but no running code").

Change permissions any time in chat: "stop running code", "you can push for the next hour" (only that category expires), "back to normal prompts". Saying "no" to a proposal changes nothing.

## How it works

| Piece | Hook | Job |
|---|---|---|
| Intent reader | `UserPromptSubmit` | Reads only messages **you** type. Turns permission talk into category approvals (Haiku via `claude -p`, with a keyword fallback). |
| Enforcer | `PreToolUse` | Maps every tool call to categories and answers `allow` or `deny`. Never `ask`, so nothing stalls. |
| Grant store | | `~/.claude/preflight/<session>.json`, expires after 8h by default |
| Log | | `~/.claude/preflight/<session>.log.jsonl` |

Until you approve something in a session, both hooks are silent and Claude Code behaves normally.

Categories: `read` (on by default), `edit`, `run`, `install`, `web`, `git`, `publish`, `message`, `apps`, `delete`. Run `python3 preflight.py categories` to list them.

`apps` covers acting in apps and websites. Looking at web pages only needs `web`. Clicking, typing, submitting forms or running JS in a browser also needs `apps`, because on a logged-in site a click can send an email.

Always blocked while active: `sudo`, changing permission modes or settings, editing anything under `.claude/` or preflight's own files, and writing to places that run code later or hold credentials: shell startup files, `~/Library/LaunchAgents`, `.git/hooks`, `~/.ssh`, `crontab`, `/etc`.

## Commands

```bash
python3 preflight.py status   # what's approved right now
python3 preflight.py report   # what ran and what was blocked
python3 -m unittest tests/test_preflight.py   # classifier, bypass attempts, decisions (offline)
python3 tests/eval_parser.py                  # how messages are understood (calls the model)
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
- Pasted text counts as your words. Pasting something that says "allow X" can grant X.
- Understanding a permission message adds about 4s, only on messages that talk about permissions. In testing, 1 in 30 everyday coding messages triggered it.
