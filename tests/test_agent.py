import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests
from agent.llm import OfflinePolicy
from agent.loop import Agent
from agent.policy import Policy
from agent.tools import Toolbox
from agent import memory
from mock_server import serve

serve(8790); BASE = "http://127.0.0.1:8790"
POL = Policy({"post_allowed_prefixes": ["/internal/bills"], "approval_required_over_amount": 10000})
quiet = lambda *a: None

class T(unittest.TestCase):
    def setUp(self): requests.get(BASE + "/__reset")
    def run_agent(self, task, approve, policy=POL):
        a = Agent(OfflinePolicy(), Toolbox(BASE), approve, lambda q: "globex", log=quiet, policy=policy)
        return a, a.run(task)

    def test_policy(self):
        self.assertEqual(POL.check("http_post", {"url": "/vendor/x", "fields": {}})[0], "deny")
        self.assertEqual(POL.check("http_post", {"url": "/internal/bills", "fields": {"amount": "50"}})[0], "approve")
        self.assertEqual(POL.check("http_post", {"url": "/internal/bills", "fields": {"amount": "15000.00"}})[0], "approve_high")

    def test_happy_path_and_verified(self):
        _, r = self.run_agent("latest acme invoice", lambda *a: True)
        self.assertTrue(r["success"])
        self.assertEqual(json.loads(requests.get(BASE + "/internal/bills").text)[0]["invoice_no"], "INV-1077")

    def test_high_amount_requires_human_and_denial_blocks_write(self):
        seen = []
        def approve(n, a, verdict="approve", reason=""): seen.append(verdict); return False
        _, r = self.run_agent("latest globex invoice", approve)
        self.assertIn("approve_high", seen)
        self.assertEqual(json.loads(requests.get(BASE + "/internal/bills").text), [])
        self.assertFalse(r["success"])  # a denied write must never be reported as success

    def test_guard_blocks_unverified_success(self):
        msg = Agent._finish_guard({"success": True, "evidence": "x"}, 3, False, False)
        self.assertIn("read it back", msg)

    def test_lessons_from_failures(self):
        trace = [{"tool": "http_post", "args": {"url": "/internal/bills"}, "decision": "approved",
                  "result": {"ok": False, "status": 422, "body": "due_date must be YYYY-MM-DD"}}]
        self.assertIn("422", memory.derive_lessons(trace)[0])

if __name__ == "__main__":
    unittest.main()
