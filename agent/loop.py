"""The agent loop: plan -> act -> observe -> decide, with guardrails the model can't talk its way past."""
import json
import time

from .tools import Toolbox

SYSTEM = """You are an autonomous AI task worker operating inside a company network via tools.
Given a natural-language goal, work out the steps yourself; don't wait to be told them.

Rules:
- Explore before acting: read pages to discover what exists (start at / or /vendor/ if unsure).
- After every tool result, decide the next step from what actually happened.
- If an action fails, read the error. Fix the cause (e.g. reformat a field) or retry if it is transient (5xx).
  Never repeat an identical failing call more than twice; change approach or ask the user.
- Use `remember` for facts you will need later (invoice number, amount, due date...).
- If the goal is ambiguous (which vendor? which invoice?) call `ask_user` instead of guessing.
- Writes (http_post) need human approval; explain nothing, just call the tool.
- Never claim success from the POST response alone. Read the record back from the system of record,
  compare it with the source data, then call `finish` with concrete evidence.
- Be concise."""

MAX_STEPS = 25
MAX_IDENTICAL_FAILS = 3


class Agent:
    def __init__(self, llm, tools: Toolbox, approve, ask, log=print):
        self.llm, self.tools, self.approve, self.ask, self.log = llm, tools, approve, ask, log
        self.trace = []

    def run(self, task: str) -> dict:
        messages = [{"role": "user", "content": task}]
        wrote_ok_at = None      # index of last successful mutating call
        verified_after = False  # a read happened after that write
        last_write_failed = False
        fail_counts: dict[str, int] = {}

        for step in range(1, MAX_STEPS + 1):
            blocks = self.llm.next_step(SYSTEM, messages, self.tools.SCHEMAS)
            messages.append({"role": "assistant", "content": blocks})
            for b in blocks:
                if b["type"] == "text" and b["text"].strip():
                    self.log(f"  [thought] {b['text'].strip()}")
            uses = [b for b in blocks if b["type"] == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": "Continue: use a tool, or call finish."})
                continue

            results = []
            final = None
            for u in uses:
                name, args = u["name"], u["input"]
                self.log(f"[{step}] {name} {json.dumps(args)[:200]}")
                sig = name + json.dumps(args, sort_keys=True)

                if name == "finish":
                    problem = self._finish_guard(args, wrote_ok_at, verified_after, last_write_failed)
                    if problem:
                        self.log(f"    guard: finish rejected - {problem}")
                        res = {"ok": False, "error": problem}
                    else:
                        final = args
                        res = {"ok": True}
                elif name == "ask_user":
                    answer = self.ask(args.get("question", ""))
                    res = {"ok": True, "answer": answer}
                elif name in Toolbox.mutating and not self.approve(name, args):
                    res = {"ok": False, "error": "human denied this action; ask what to do instead of retrying"}
                else:
                    res = self.tools.run(name, args)
                    if name in Toolbox.mutating:
                        last_write_failed = not res.get("ok")
                        if res.get("ok"):
                            wrote_ok_at, verified_after = step, False
                    elif name == "http_get" and wrote_ok_at is not None and res.get("ok"):
                        verified_after = True

                if not res.get("ok") and name != "finish":
                    fail_counts[sig] = fail_counts.get(sig, 0) + 1
                    if fail_counts[sig] >= MAX_IDENTICAL_FAILS:
                        res["hint"] = "Same call failed repeatedly. Change approach or ask_user; do not repeat it."
                    if res.get("retryable"):
                        time.sleep(0.3)  # tiny backoff
                self.log(f"    -> {json.dumps(res)[:300]}")
                self.trace.append({"step": step, "tool": name, "args": args, "result": res})
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(res)})

            messages.append({"role": "user", "content": results})
            if final:
                return {**final, "steps": step, "memory": self.tools.memory}
        return {"success": False, "summary": f"Stopped: step budget ({MAX_STEPS}) exhausted.",
                "evidence": json.dumps(self.trace[-3:])[:500], "steps": MAX_STEPS, "memory": self.tools.memory}

    @staticmethod
    def _finish_guard(args, wrote_ok_at, verified_after, last_write_failed):
        if args.get("success"):
            if last_write_failed:
                return "your last write failed; you cannot report success"
            if wrote_ok_at is not None and not verified_after:
                return "you wrote data but haven't read it back; GET the system of record and compare first"
            if not str(args.get("evidence", "")).strip():
                return "evidence is required"
        return None
