"""Tool layer: the only way the agent touches the world. Every tool returns a plain dict
{ok, ...} so the loop can observe success/failure uniformly."""
import re
from html import unescape
from urllib.parse import urlencode, urljoin

import requests

TIMEOUT = 10
MAX_BODY = 4000


def _text(body: str, ctype: str) -> str:
    if "html" in ctype:
        body = re.sub(r"</(tr|li|h\d|p)>", "\n", body)
        body = re.sub(r"</t[dh]>", " | ", body)
        body = re.sub(r"<a [^>]*href='([^']+)'[^>]*>", r"[link:\1] ", body)
        body = unescape(re.sub(r"<[^>]+>", "", body))
    return body.strip()[:MAX_BODY]


class Toolbox:
    """Holds base URL + working memory. `mutating` marks tools that need human approval."""

    mutating = {"http_post"}

    def __init__(self, base_url: str):
        self.base = base_url
        self.memory: dict[str, str] = {}

    # --- tool schemas (Anthropic tool-use format) ---
    SCHEMAS = [
        {"name": "http_get", "description": "GET a page/API on the company network. Relative paths allowed (e.g. /vendor/). Returns status and text.",
         "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "http_post", "description": "POST form fields to an endpoint (e.g. enter a record into the internal system). SIDE EFFECT: requires human approval.",
         "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "fields": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["url", "fields"]}},
        {"name": "remember", "description": "Store a fact discovered during the task (key/value) for later steps.",
         "input_schema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}},
        {"name": "ask_user", "description": "Ask the human for clarification when the task is ambiguous or you cannot safely proceed.",
         "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
        {"name": "finish", "description": "End the task. Only call after verifying the outcome by reading it back from the system of record.",
         "input_schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"}, "evidence": {"type": "string"}}, "required": ["success", "summary", "evidence"]}},
    ]

    def run(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"_{name}", None)
        if fn is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"ok": False, "error": f"bad arguments: {e}"}
        except requests.RequestException as e:
            return {"ok": False, "error": f"network error: {e}", "retryable": True}

    def _http_get(self, url: str):
        r = requests.get(urljoin(self.base, url), timeout=TIMEOUT)
        return {"ok": r.ok, "status": r.status_code, "body": _text(r.text, r.headers.get("Content-Type", ""))}

    def _http_post(self, url: str, fields: dict):
        r = requests.post(urljoin(self.base, url), data=fields, timeout=TIMEOUT,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        out = {"ok": r.ok, "status": r.status_code, "body": _text(r.text, r.headers.get("Content-Type", ""))}
        if r.status_code in (502, 503, 504, 429):
            out["retryable"] = True
        return out

    def _remember(self, key: str, value: str):
        self.memory[key] = value
        return {"ok": True, "memory": self.memory}

    def _ask_user(self, question: str):  # replaced by the loop's callback
        return {"ok": False, "error": "no user channel"}

    def _finish(self, success: bool, summary: str, evidence: str):
        return {"ok": True}
