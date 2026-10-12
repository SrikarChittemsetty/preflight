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


class UnderstandTest(unittest.TestCase):
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
