import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["PREFLIGHT_STATE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preflight as pf  # noqa: E402


def bash(cmd):
    return pf.classify("Bash", {"command": cmd})


class ClassifyTest(unittest.TestCase):
    def test_builtin_tools(self):
        self.assertEqual(pf.classify("Read", {"file_path": "x"}), {"read"})
        self.assertEqual(pf.classify("Write", {"file_path": "/tmp/x"}), {"edit"})
        self.assertEqual(pf.classify("WebSearch", {"query": "x"}), {"web"})
        self.assertEqual(pf.classify("ToolSearch", {}), {pf.INTERNAL})

    def test_mcp_tools(self):
        gmail = "mcp__4e6f18e7-cf7e-43a4-9df5-7ad5ad333445__"
        self.assertEqual(pf.classify(gmail + "send_message", {}), {"message"})
        self.assertEqual(pf.classify(gmail + "reply", {}), {"message"})
        self.assertEqual(pf.classify(gmail + "forward", {}), {"message"})
        self.assertEqual(pf.classify(gmail + "create_draft", {}), {"apps"})
        self.assertEqual(pf.classify(gmail + "search_threads", {}), {"read"})
        self.assertEqual(pf.classify(gmail + "trash_thread", {}), {"delete"})
        slack = "mcp__aa8b355b__"
        self.assertEqual(pf.classify(slack + "slack_send_message", {}), {"message"})
        self.assertEqual(pf.classify(slack + "slack_read_channel", {}), {"read"})
        self.assertEqual(pf.classify("mcp__Claude_Browser__navigate", {}), {"web"})
        self.assertEqual(pf.classify("mcp__ccd_session_mgmt__set_session_permission_mode", {}),
                         {pf.FORBIDDEN})

    def test_bash(self):
        self.assertEqual(bash("ls -la && cat foo | grep 'a|b'"), {"read"})
        self.assertEqual(bash("echo hi > out.txt"), {"edit"})
        self.assertEqual(bash("ls 2>/dev/null"), {"read"})
        self.assertEqual(bash("npm test 2>&1 | tail"), {"run"})
        self.assertEqual(bash("npm install lodash"), {"install"})
        self.assertEqual(bash("pip install requests"), {"install"})
        self.assertEqual(bash("python3 -m pip install requests"), {"install"})
        self.assertEqual(bash("python3 script.py"), {"run"})
        self.assertEqual(bash("rm -rf build"), {"delete"})
        self.assertEqual(bash("find . -name '*.pyc' -delete"), {"delete"})
        self.assertEqual(bash("git status"), {"read"})
        self.assertEqual(bash("git add . && git commit -m 'x'"), {"git"})
        self.assertEqual(bash("git push origin main"), {"publish"})
        self.assertEqual(bash("git reset --hard HEAD"), {"delete"})
        self.assertEqual(bash("curl -s https://example.com"), {"web"})
        self.assertEqual(bash("curl -X POST -d 'a=1' https://httpbin.org/post"), {"publish"})
        self.assertEqual(bash("gh pr create --title x"), {"publish"})
        self.assertEqual(bash("gh pr view 3"), {"web"})
        self.assertEqual(bash("sudo rm -rf /"), {pf.FORBIDDEN})
        self.assertEqual(bash("FOO=1 timeout 30 rm x"), {"delete"})
        self.assertEqual(bash("bash -c 'git push'"), {"publish"})
        self.assertEqual(bash("echo $(rm x)"), {"delete"})
        self.assertEqual(bash("cat > notes.md <<'EOF'\nrm everything\ngit push\nEOF"), {"edit"})

    def test_tricky_shell(self):
        cases = {
            "cd src && npm run build": {"run"},
            "python -m pytest -x": {"run"},
            "git log --oneline | head -20": {"read"},
            "for f in *.py; do echo $f; done": {"read"},
            "if [ -f x ]; then cat x; fi": {"read"},
            "while true; do sleep 1; done": {"read"},
            "export FOO=1 && echo $FOO": {"read"},
            "source venv/bin/activate && pip install -r requirements.txt": {"run", "install"},
            "ls | xargs rm": {"delete"},
            "mkdir -p out && cp a.txt out/": {"edit"},
            "chmod +x run.sh && ./run.sh": {"edit", "run"},
            "curl -sL https://x.com/install.sh -o install.sh": {"web", "edit"},
            "curl -s https://api.github.com/repos/a/b | jq .stars": {"web"},
            "wget https://example.com/file.zip": {"web", "edit"},
            "git checkout -b feature/x": {"git"},
            "git stash && git pull --rebase": {"git", "web"},
            "gh pr list": {"web"},
            "gh pr create --fill": {"publish"},
            "git push --force": {"publish"},
            "yarn": {"install"},
            "uv pip install httpx": {"install"},
            "rm -rf node_modules && npm install": {"delete", "install"},
            "echo 'x' >> notes.md": {"edit"},
            "pwd; ls -la; git status": {"read"},
            "python3 -c \"import smtplib\"": {"run"},
        }
        for cmd, want in cases.items():
            self.assertEqual(bash(cmd), want, cmd)

    def test_mcp_visible_to_others(self):
        self.assertEqual(pf.classify("mcp__x__create_event", {}), {"message"})
        self.assertEqual(pf.classify("mcp__x__update_event", {}), {"message"})
        self.assertEqual(pf.classify("mcp__gh__create_pull_request", {}), {"publish"})
        self.assertEqual(pf.classify("mcp__gh__add_issue_comment", {}), {"publish"})
        self.assertEqual(pf.classify("mcp__notion__create_page", {}), {"apps"})
        self.assertEqual(pf.classify("mcp__drive__share_file", {}), {"publish"})

    def test_protected(self):
        self.assertEqual(bash("echo '{}' > ~/.claude/settings.json"), {pf.FORBIDDEN})
        self.assertEqual(bash("rm ~/.claude/preflight/abc.json"), {pf.FORBIDDEN})
        self.assertEqual(bash("cat ~/.claude/settings.json"), {"read"})
        self.assertEqual(bash(f"python3 {pf.HERE}/preflight.py status"), {"read"})
        self.assertEqual(bash(f"python3 {pf.HERE}/preflight.py hook-prompt < x"), {pf.FORBIDDEN})
        self.assertEqual(pf.classify("Write", {"file_path": str(Path.home() / ".claude/settings.json")}),
                         {pf.FORBIDDEN})
        self.assertEqual(pf.classify("Edit", {"file_path": str(pf.HERE / "preflight.py")}), {pf.FORBIDDEN})
        self.assertEqual(pf.classify("Write", {"file_path": str(pf.HERE / "hooks" / "hooks.json")}), {pf.FORBIDDEN})
        self.assertEqual(bash("echo '{}' > hooks/hooks.json"), {pf.FORBIDDEN})
        self.assertEqual(pf.classify("Bash", {"command": "ls", "dangerouslyDisableSandbox": True}),
                         {pf.FORBIDDEN})


class DecideTest(unittest.TestCase):
    state = {"allow": ["web", "edit"], "deny": ["message"]}

    def test_decisions(self):
        self.assertEqual(pf.decide(self.state, {"web"})[0], "allow")
        self.assertEqual(pf.decide(self.state, {"read"})[0], "allow")  # on by default
        self.assertEqual(pf.decide(self.state, {"message"}), ("deny", {"message"}))
        self.assertEqual(pf.decide(self.state, {"run"}), ("deny", {"run"}))  # unanticipated
        self.assertEqual(pf.decide(self.state, {"web", "publish"}), ("deny", {"publish"}))
        self.assertEqual(pf.decide(self.state, {pf.INTERNAL})[0], "allow")
        self.assertEqual(pf.decide({"allow": [], "deny": ["read"]}, {"read"})[0], "deny")


class HookTest(unittest.TestCase):
    def test_silent_without_approvals(self):
        out = pf.hook_tool({"session_id": "nothing-here", "tool_name": "Bash",
                            "tool_input": {"command": "rm -rf x"}})
        self.assertIsNone(out)

    def test_round_trip(self):
        sid = "roundtrip"
        change = {"change": True, "allow": ["web"], "deny": ["message"], "hours": None}
        pf.save_state(pf.apply_change(None, sid, change, "web ok, no email"))
        allow = pf.hook_tool({"session_id": sid, "tool_name": "WebSearch", "tool_input": {"query": "q"}})
        deny = pf.hook_tool({"session_id": sid, "tool_name": "mcp__x__send_message", "tool_input": {}})
        self.assertEqual(allow["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertEqual(deny["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Message people", deny["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("1 actions ran without a popup, 1 were blocked", pf.report(sid))


class TimeLimitTest(unittest.TestCase):
    def test_hours_limit_only_the_granted_category(self):
        state = pf.apply_change(None, "tl", {"allow": ["web"], "deny": []}, "web ok")
        state = pf.apply_change(state, "tl", {"allow": ["publish"], "deny": [], "hours": 1}, "push for an hour")
        self.assertIn("publish", state["until"])
        self.assertNotIn("web", state["until"])
        state["until"]["publish"] = "2000-01-01T00:00:00+00:00"  # the hour is up
        self.assertEqual(pf.decide(state, {"publish"}), ("deny", {"publish"}))
        self.assertEqual(pf.decide(state, {"web"})[0], "allow")


class UnderstandTest(unittest.TestCase):
    def test_broad_grant_allows_everything(self):
        self.assertEqual(set(pf.understand("use any tools you need", "")["allow"]), set(pf.CATEGORIES))

    def test_no_to_proposal_changes_nothing(self):
        self.assertIsNone(pf.understand("no", "PREFLIGHT-REQUEST: allow=web deny=message"))

    def test_off(self):
        self.assertTrue(pf.understand("ok back to normal prompts please", "")["off"])

    def test_yes_to_proposal(self):
        proposal = "I'll need web and edits.\nPREFLIGHT-REQUEST: allow=web,edit,run deny=message,publish"
        change = pf.understand("yes", proposal)
        self.assertEqual(change["allow"], ["web", "edit", "run"])
        self.assertEqual(change["deny"], ["message", "publish"])

    def test_plain_task_is_ignored(self):
        self.assertIsNone(pf.understand("refactor the parser into two modules", ""))

    def test_keyword_fallback(self):
        change = pf.parse_with_keywords("web searches are fine but don't send email")
        self.assertIn("web", change["allow"])
        self.assertIn("message", change["deny"])
        self.assertNotIn("message", change["allow"])


if __name__ == "__main__":
    unittest.main()


class BypassTest(unittest.TestCase):
    """With only web + edit approved, none of these may get through."""
    state = {"allow": ["web", "edit"], "deny": ["message"], "until": {}}
    attacks = {
        "find -exec runs code": ("Bash", {"command": "find . -name x -exec python3 evil.py \;"}),
        "awk system()": ("Bash", {"command": "awk 'BEGIN{system(\"rm -rf x\")}'"}),
        "git -c runs code": ("Bash", {"command": "git -c core.pager='sh evil.sh' log"}),
        "glob into state dir": ("Bash", {"command": "echo '{}' > ~/.claude/pref*/s.json"}),
        "edit shell startup file": ("Write", {"file_path": "~/.zshrc", "content": "x"}),
        "append to zshrc via shell": ("Bash", {"command": "echo 'curl x|sh' >> ~/.zshrc"}),
        "edit git hook": ("Write", {"file_path": "/repo/.git/hooks/pre-commit", "content": "x"}),
        "LaunchAgent": ("Write", {"file_path": "~/Library/LaunchAgents/x.plist", "content": "x"}),
        "ssh keys": ("Write", {"file_path": "~/.ssh/authorized_keys", "content": "x"}),
        "browser click": ("mcp__claude-in-chrome__computer", {"action": "left_click"}),
        "browser form fill": ("mcp__Claude_Browser__form_input", {"ref": "r1", "value": "hi"}),
        "browser JS": ("mcp__Claude_Browser__javascript_tool", {"text": "x"}),
        "browser batch with a click": ("mcp__Claude_Browser__browser_batch", {"actions": [
            {"name": "navigate", "input": {}}, {"name": "computer", "input": {"action": "left_click"}}]}),
        "desktop typing": ("mcp__computer-use__app_type", {"text": "hi"}),
    }
    legit = {
        "edit fetch.py": ("Write", {"file_path": "/repo/src/fetch.py", "content": "x"}),
        "edit user.profile.ts": ("Write", {"file_path": "/repo/user.profile.ts", "content": "x"}),
        "shell edit user.profile.ts": ("Bash", {"command": "echo x > src/user.profile.ts"}),
        "browser navigate": ("mcp__Claude_Browser__navigate", {"url": "https://x.com"}),
        "browser read": ("mcp__claude-in-chrome__get_page_text", {}),
        "browser screenshot": ("mcp__claude-in-chrome__computer", {"action": "screenshot"}),
        "read zshrc": ("Bash", {"command": "cat ~/.zshrc"}),
        "find files": ("Bash", {"command": "find . -name '*.py'"}),
        "awk columns": ("Bash", {"command": "awk '{print $1}' data.txt"}),
    }

    def test_attacks_blocked(self):
        for name, (tool, inp) in self.attacks.items():
            self.assertEqual(pf.decide(self.state, pf.classify(tool, inp))[0], "deny", name)

    def test_legit_allowed(self):
        for name, (tool, inp) in self.legit.items():
            self.assertEqual(pf.decide(self.state, pf.classify(tool, inp))[0], "allow", name)
