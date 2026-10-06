"""Persistent memory + prompt assembly: company context, lessons from past runs, audit logs."""
import json
import os
import time

MEM_DIR = "memory"
LOG_DIR = "logs"


def load_company(path="company.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _lessons_path():
    return os.path.join(MEM_DIR, "learnings.jsonl")


def load_lessons(n=8) -> list[str]:
    if not os.path.exists(_lessons_path()):
        return []
    seen, out = set(), []
    with open(_lessons_path(), encoding="utf-8") as f:
        for line in reversed(f.readlines()):
            for l in json.loads(line).get("lessons", []):
                if l not in seen:
                    seen.add(l); out.append(l)
    return out[:n]


def derive_lessons(trace: list[dict]) -> list[str]:
    """Automatically distil failures the agent hit (and the error text) so later runs avoid them."""
    out = []
    for t in trace:
        r = t["result"]
        if t["tool"] == "http_post" and not r.get("ok") and not r.get("retryable"):
            out.append(f"POST {t['args'].get('url')} failed {r.get('status')}: {str(r.get('body', r.get('error')))[:140]}")
        elif t.get("decision") == "denied-by-policy":
            out.append(f"Policy blocked: {r.get('error')}")
    return out


def record_run(task, result, trace):
    os.makedirs(MEM_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    with open(_lessons_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "task": task, "success": result.get("success"),
                            "lessons": derive_lessons(trace)}) + "\n")
    path = os.path.join(LOG_DIR, f"run-{ts}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"task": task, "result": {k: v for k, v in result.items() if k != "memory"}}) + "\n")
        for t in trace:
            f.write(json.dumps({**t, "result": {k: (str(v)[:400]) for k, v in t["result"].items()}}) + "\n")
    return path


def build_system(base: str, company: dict, lessons: list[str]) -> str:
    ctx = {k: company[k] for k in ("company", "systems", "procedures") if k in company}
    s = base + "\n\nCOMPANY CONTEXT (authoritative; follow these procedures):\n" + json.dumps(ctx, indent=1)
    s += f"\nWrites outside {company.get('policy', {}).get('post_allowed_prefixes')} are blocked by policy; large amounts need human approval."
    if lessons:
        s += "\n\nLESSONS FROM PAST RUNS (avoid repeating these mistakes):\n- " + "\n- ".join(lessons)
    return s
