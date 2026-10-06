#!/usr/bin/env python3
"""Usage:
  python run.py "Find the latest invoice from Acme, extract amount and due date, enter it into the internal system"
  python run.py --offline "..."        # no API key: rule-based stand-in policy (harness demo only)
  python run.py --auto-approve "..."   # skip the human approval prompt for writes
"""
import argparse
import json
import os
import sys

import requests

from agent.llm import ClaudeLLM, OfflinePolicy, OpenAICompatLLM
from agent.loop import Agent
from agent.tools import Toolbox
from mock_server import serve

PORT = 8765


def make_llm(offline):
    env = os.environ
    if not offline:
        if env.get("GEMINI_API_KEY"):
            m = env.get("OPS_AGENT_MODEL", "gemini-3.8-flash")
            print(f"LLM: Gemini ({m}) via OpenAI-compatible API\n")
            return OpenAICompatLLM("https://generativelanguage.googleapis.com/v1beta/openai", env["GEMINI_API_KEY"], m)
        if env.get("OPENAI_API_KEY") and env.get("OPENAI_BASE_URL"):  # Groq / OpenRouter / Ollama / OpenAI
            m = env.get("OPS_AGENT_MODEL", "gpt-4o-mini")
            print(f"LLM: {m} at {env['OPENAI_BASE_URL']}\n")
            return OpenAICompatLLM(env["OPENAI_BASE_URL"], env["OPENAI_API_KEY"], m)
        if env.get("ANTHROPIC_API_KEY"):
            print("LLM: Claude via Anthropic API\n")
            return ClaudeLLM()
    print("(!) Offline stand-in policy in use - set GEMINI_API_KEY (or another provider key) for the real LLM-driven agent.\n")
    return OfflinePolicy()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("task")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--auto-approve", action="store_true")
    a = p.parse_args()

    serve(PORT)
    base = f"http://127.0.0.1:{PORT}"
    requests.get(base + "/__reset")

    llm = make_llm(a.offline)

    def approve(name, args):
        if a.auto_approve:
            print(f"    (auto-approved {name})")
            return True
        return input(f"\n  APPROVE {name} {json.dumps(args)}? [y/N] ").strip().lower() == "y"

    def ask(q):
        return input(f"\n  AGENT ASKS: {q}\n  > ")

    agent = Agent(llm, Toolbox(base), approve, ask)
    print(f"TASK: {a.task}\n")
    out = agent.run(a.task)
    print("\n=== RESULT ===")
    print(json.dumps(out, indent=2))
    print("\n=== Ground truth in internal system ===")
    print(requests.get(base + "/internal/bills").text)
    sys.exit(0 if out.get("success") else 1)


if __name__ == "__main__":
    main()
