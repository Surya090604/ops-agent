"""LLM backends. The loop only needs: next_step(system, messages, tools) -> list of content blocks.

* ClaudeLLM  - real model via the Anthropic Messages API (tool use). This is the real agent.
* OfflinePolicy - tiny rule-based stand-in so the loop, retries, approvals and verification guard
  can be demoed/tested with no API key. It is NOT general; it only knows the invoice workflow and
  exists to exercise the harness. Be explicit about this in demos.
"""
import json
import os
import re

import requests

MODEL = os.environ.get("OPS_AGENT_MODEL", "claude-sonnet-5-5")


class ClaudeLLM:
    def __init__(self):
        self.key = os.environ["ANTHROPIC_API_KEY"]

    def next_step(self, system, messages, tools):
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 1500, "system": system, "messages": messages, "tools": tools},
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["content"]


class OpenAICompatLLM:
    """Any OpenAI-compatible chat-completions endpoint with tool calling (Gemini, Groq, OpenRouter, Ollama).
    Internal message format is Anthropic-style blocks; this class translates both ways."""

    def __init__(self, base_url, api_key, model):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model = api_key, model

    @staticmethod
    def _convert(system, messages, google=False):
        out = [{"role": "system", "content": system}]
        for m in messages:
            if isinstance(m["content"], str):
                out.append({"role": m["role"], "content": m["content"]})
            elif m["role"] == "assistant":
                text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
                calls = []
                for b in m["content"]:
                    if b["type"] != "tool_use":
                        continue
                    c = {"id": b["id"], "type": "function",
                         "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                    if b.get("extra"):  # Gemini 3: thought_signature must be echoed back verbatim
                        c["extra_content"] = b["extra"]
                    elif google:
                        c["extra_content"] = {"google": {"thought_signature": "skip_thought_signature_validator"}}
                    calls.append(c)
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                    elif b["type"] == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    def next_step(self, system, messages, tools):
        body = {"model": self.model, "messages": self._convert(system, messages, "googleapis.com" in self.url),
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools]}
        for attempt in range(4):  # free tiers rate-limit; back off on 429/5xx
            r = requests.post(self.url, headers={"Authorization": f"Bearer {self.key}"}, json=body, timeout=90)
            if r.status_code in (429, 500, 502, 503) and attempt < 3:
                import time; time.sleep(2 * (attempt + 1)); continue
            break
        if not r.ok:
            raise RuntimeError(f"LLM API error {r.status_code}: {r.text[:300]}")
        msg = r.json()["choices"][0]["message"]
        blocks = []
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for i, c in enumerate(msg.get("tool_calls") or []):
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            blk = {"type": "tool_use", "id": c.get("id") or f"call_{i}_{abs(hash(str(c)))%10**6}",
                   "name": c["function"]["name"], "input": args}
            if c.get("extra_content"):
                blk["extra"] = c["extra_content"]
            blocks.append(blk)
        return blocks


class OfflinePolicy:
    """Reads the last observation and decides the next tool call. Stateless except via history."""

    def __init__(self):
        self.n = 0

    def _id(self):
        self.n += 1
        return f"off_{self.n}"

    def _call(self, name, **inp):
        return [{"type": "tool_use", "id": self._id(), "name": name, "input": inp}]

    def next_step(self, system, messages, tools):
        task = messages[0]["content"].lower()
        calls = []  # (name, input, result_text)
        pending = {}
        for m in messages[1:]:
            if m["role"] == "assistant":
                for b in m["content"]:
                    if b["type"] == "tool_use":
                        pending[b["id"]] = (b["name"], b["input"])
            else:
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        calls.append((*pending[b["tool_use_id"]], b["content"]))
        haystack = task + " " + " ".join(c[2].lower() for c in calls if c[0] == "ask_user")
        vendor = next((v for v in ("acme", "globex") if v in haystack), None)
        if not vendor:
            return self._call("ask_user", question="Which vendor's invoice should I process (acme or globex)?")
        if not calls:
            return self._call("http_get", url=f"/vendor/invoices?company={vendor}")
        name, inp, res = calls[-1]
        if name == "ask_user":
            return self._call("http_get", url=f"/vendor/invoices?company={vendor}")
        if name == "http_get" and "/vendor/invoices" in inp["url"]:
            body = json.loads(res).get("body", "")
            rows = re.findall(r"^(\S+) \| (\w{3} \d{2}, \d{4}) \| \$([\d,.]+) \| (\w{3} \d{2}, \d{4})", body, re.M)
            from datetime import datetime
            f = lambda s: datetime.strptime(s, "%b %d, %Y")
            no, issued, amt, due = max(rows, key=lambda r: f(r[1]))
            self.pending = dict(vendor=vendor, invoice_no=no, amount=amt.replace(",", ""), due_date=f(due).strftime("%Y-%m-%d"))
            return self._call("http_post", url="/internal/bills", fields=self.pending)
        if name == "http_post":
            if '"status": 503' in res or "503" in res[:40]:
                return self._call("http_post", url="/internal/bills", fields=inp["fields"])  # retry
            return self._call("http_get", url="/internal/bills")  # verify
        if name == "http_get" and "/internal/bills" in inp["url"]:
            return self._call("finish", success=True,
                              summary=f"Recorded latest {vendor} invoice in the internal system.",
                              evidence=res[:500])
        return self._call("finish", success=False, summary="Offline policy stuck", evidence=res[:200])
