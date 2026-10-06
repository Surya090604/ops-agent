# ops-agent: Autonomous AI Task Worker (prototype)

Takes a natural-language task, explores a small company network with tools, recovers from failures,
verifies the result in the system of record, and returns a summary with evidence.

```
python run.py "Find the latest invoice from Acme, extract the amount and due date, enter it into our internal system, and tell me once it is done."
```

## Setup
```
pip install requests
export GEMINI_API_KEY=...                   # free key: https://aistudio.google.com/apikey  (default model gemini-3.8-flash)
python run.py "<task>"                      # prompts you to approve each write
python run.py --auto-approve "<task>"       # skip approval prompts
python run.py --offline --auto-approve "<task>"   # no key: rule-based stand-in, harness demo only
```
Other providers: set `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPS_AGENT_MODEL` (Groq, OpenRouter, Ollama, OpenAI),
or `ANTHROPIC_API_KEY` for Claude. Override the model with `OPS_AGENT_MODEL`. Only dependency: `requests`.

## The environment (mock, no real systems)
`mock_server.py` runs locally and simulates:
- **Vendor portal** (`/vendor/`, `/vendor/invoices?company=acme|globex`), HTML tables, human-formatted dates/amounts, list NOT sorted by date.
- **Internal billing system** (`POST/GET /internal/bills`) that is deliberately strict/flaky:
  first write returns **503**; `due_date` must be `YYYY-MM-DD` and `amount` a plain number (**422** otherwise); duplicates return **409**.

## Architecture
```
run.py            CLI, starts mock server, approval + clarification prompts
agent/loop.py     plan -> act -> observe -> decide loop + guardrails
agent/tools.py    http_get, http_post, remember, ask_user, finish  (uniform {ok,...} results)
agent/llm.py      OpenAICompatLLM (Gemini/Groq/OpenRouter/Ollama) | ClaudeLLM | OfflinePolicy (test stand-in)
mock_server.py    the sandbox company network
```
The model chooses every action; the loop owns safety and reliability.

## Key design decisions
- **Model decides, harness enforces.** Nothing about invoices is hard-coded in the loop or tools; swapping the task means swapping the prompt (and environment), not the agent. That is the generalization story.
- **Verification guard.** `finish(success=true)` is rejected if (a) the last write failed, or (b) a write succeeded but no read-back from the system of record followed, or (c) no evidence is given. The model can't claim success from a POST response.
- **Human approval for side effects.** Mutating tools (`http_post`) require approval; denial is fed back as an observation. `ask_user` handles ambiguity (e.g. "process the latest invoice" with no vendor).
- **Failure handling.** Errors are returned as observations, not exceptions: 5xx marked `retryable` (short backoff), 4xx messages are readable so the model can fix its input (e.g. reformat the date), and an identical call failing 3x triggers a hint to change approach. Hard step budget of 25.
- **Working memory** via `remember`, plus the full message history.
- **Plain HTTP tools instead of a browser** to keep the prototype narrow but genuinely working. A browser tool would slot in as another entry in `Toolbox`.

## Known limitations
- Only HTTP tools; no real browser/computer use (no JS rendering, clicking, screenshots).
- `OfflinePolicy` is a scripted stand-in that only knows the invoice flow; it exists to test the harness without an API key. It is **not** the autonomous agent. The Claude-driven path is the real one, and I have not yet run it end-to-end in this build (no API key in the build environment), so expect to tune the prompt.
- Verification compares nothing automatically: the model reads back and judges. No programmatic diff against source data.
- No persistent memory across runs, no cost/token tracking, single task at a time, no auth handling.
- Approval is per-call on any POST; there is no policy on risk level.

## What I'd build next
1. Playwright browser tool + screenshot/DOM observations.
2. Declarative per-task verifiers (assert fields match source) so verification doesn't rely on the model alone.
3. Eval suite: many task variants + fault injection (timeouts, wrong vendor, duplicate records, changed layouts) with pass/fail metrics.
4. Risk-tiered approvals, idempotency keys for writes, persistent memory, tracing UI.

## Assumptions
- "Latest invoice" = most recent **issue date**. Amount and dates normalized to the internal system's formats.
- Everything runs against the local mock; no real credentials or third-party systems.

## Models / services / components
Gemini (OpenAI-compatible endpoint) by default, or any OpenAI-compatible / Anthropic API, via `requests`; Python stdlib `http.server` for the mock app. No agent frameworks.
