# Reasoning models return empty output (the overnight-briefing `{}` saga)

_Status: resolved. Branch: `ollama-reasoning-empty-output`._

## TL;DR

The overnight briefing (and any `chat_json` call) blew up or came back empty
when the configured model was a **reasoning/"thinking" model** (e.g.
`gemma4:26b`). There was **no single cause** — it was four stacked problems that
we peeled off one at a time:

1. `chat_json` did a raw `json.loads()` on empty content → opaque 500.
2. Thinking models spend their output on chain-of-thought → empty `content`.
3. **JSON grammar mode (`format=json`) + thinking** → the model answers _inside_
   its thinking channel and satisfies the grammar with an empty `{}`.
4. **`num_ctx` too small** (Ollama's default 4096) → thinking fills the context
   window before the answer is written → empty content, `done=length`.

The final fix moves our LLM calls onto Ollama's **native `/api/chat`** endpoint
(over `requests`, no new dependency) so we can, per request:

- set `think` to `false` or a graded level (`low`/`medium`/`high`/`max`),
- **drop the JSON grammar constraint when thinking is on**,
- set **`num_ctx`** (the OpenAI-compat `/v1` endpoint cannot),
- set `num_predict` (output ceiling).

Plus user-facing settings for **Thinking effort**, **Output token limit**, and
**Context window** on both the default chat model and the overnight briefing.

## Original symptom

Clicking "Generate now" for the overnight briefing 500'd:

```
File "backend/ai/llm.py", line 30, in chat_json
    return json.loads(resp.choices[0].message.content)
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

`resp.choices[0].message.content` was an **empty string**, and `json.loads('')`
throws. The active model was `gemma4:26b` (a reasoning model) as the briefing
model, `gemma4:e4b` as the chat model.

## The investigation, in order

### 1. Empty/opaque parse → tolerant parser + clear error

`chat_json` had no guard against empty or fenced content. Fixed with
`_parse_json_response`, which:

- coalesces `None`, strips `<think>…</think>` blocks and ` ```json ` fences,
- falls back to extracting the first `{…}` object from surrounding prose,
- raises a clear `EmptyCompletion(ValueError)` (with a snippet) instead of a bare
  `JSONDecodeError`.

The `/briefing/run` route now catches `EmptyCompletion` and returns a readable
**502**, not a raw traceback.

### 2. Thinking eats the output → `reasoning_effort`

Probing `gemma4:26b` in JSON mode showed a populated `reasoning` field and
`finish_reason: length`, with `content` empty or a degenerate loop
(`"recharge-and recharge-and…"`). The model spent its whole budget thinking.

Per Ollama's source (`openai/openai.go`), the `/v1` endpoint accepts
`reasoning_effort ∈ {none, low, medium, high, max}` and maps it to the native
`think` value (`none` → `think=false`). Empirically:

- `think: false` (extra_body) → **ignored**.
- `reasoning_effort='none'` → **works** — suppresses thinking, clean JSON.
  Accepted harmlessly by non-reasoning models too (tested `mistral`).

So `chat_json` defaults to `reasoning_effort='none'`. Verified end to end: the
26B produced a full 702-char briefing.

### 3. The graded-level red herring vs. the real `format=json` collision

We exposed `reasoning_effort` as a graded **Thinking effort** setting
(none/low/medium/high/max). But turning it on still failed on the 26B — even at
"low", even with a huge `max_tokens`. Two experiments cracked it:

- With `format=json` **on**, `think=low`, generous `num_ctx`/`num_predict`:
  `content = "{}"` (2 chars), `thinking_len ≈ 1210`, `done=stop`.
  → The model **answers inside the thinking channel** and satisfies the JSON
  grammar with the shortest valid object, `{}`.
- With `format=json` **off** (same everything else): real JSON came out
  (` ```json {"briefing": …, "todos": […]} ` ), which our tolerant parser
  already handles.

**Lesson: JSON grammar mode and thinking are mutually exclusive in practice.**
Drop the grammar constraint whenever thinking is enabled.

### 4. `num_ctx` is the other half — and the `/v1` endpoint can't set it

Even with the grammar dropped, the _real_ briefing (a ~1300-token prompt) still
came back empty through our client. The discriminator:

```
real briefing prompt, think=low, NO format, native /api/chat:
[num_ctx= 4096] content_len=   0  thinking_len=9444   done=length   ❌
[num_ctx=16384] content_len=1228  thinking_len=11384  done=stop     ✅
```

At `num_ctx=4096`, the model's "low" thinking is ~2400+ tokens; prompt +
thinking exhaust the window and it hits `done=length` **before writing any
content**. At 16384 there's room for prompt + thinking + answer.

Ollama's default `num_ctx` is **4096**, and — confirmed in their docs and
empirically (a 256-token window still recalled a codeword ~2000 tokens back) —
the **OpenAI-compat `/v1` endpoint silently ignores `num_ctx`**
(`options.num_ctx` in `extra_body` does nothing). You can only set it via:

- the native `/api/chat` `options.num_ctx`, or
- `OLLAMA_CONTEXT_LENGTH=… ollama serve` (global), or
- a Modelfile `PARAMETER num_ctx …`.

We chose per-request control → the native endpoint.

## Red herring worth remembering: the sliding context window

An early isolation test made me wrongly conclude `num_ctx` didn't matter: with a
**short** prompt and a small model, `num_ctx=512` still produced ~950 tokens of
output. Ollama/llama.cpp does **context shifting during generation** (old tokens
scroll out of attention and it keeps going), so a small window doesn't truncate a
_short_-prompt generation.

It only bites when the prompt is **already large** and thinking is verbose: the
window fills during thinking and generation stops (`done=length`) before content.
The real-prompt test above is what exposed it. Moral: **reproduce with the real
(large) prompt**, not a toy one.

## The fix (what shipped)

### Backend

- **`backend/ai/llm.py`** — rewritten onto Ollama's native `/api/chat` via
  `requests`:
  - `_native_body(...)` builds the request: maps `reasoning_effort` → `think`
    (`none`→`false`, else the level), sets `format='json'` **only when not
    thinking**, and packs `options.num_ctx` / `options.num_predict`.
  - `_native_chat` (blocking) and `_native_chat_stream` (NDJSON) transports.
  - `chat_json`, `chat_text`, `chat_messages` route through it;
    `default_generation_opts()` returns `{reasoning_effort, num_ctx,
num_predict}` from settings.
  - `chat_with_tools` **stays** on the OpenAI-compat client — tool calling
    doesn't use these knobs and its message shape is consumed elsewhere.
  - `_parse_json_response` / `EmptyCompletion` from step 1.
- **`backend/ai/chat.py`** — `chat_stream` yields from `_native_chat_stream`
  (content deltas only; the separate `thinking` field is intentionally hidden).
- **`backend/db/connection.py`** — new idempotent columns:
  `llm_reasoning_effort`, `llm_max_tokens`, `llm_num_ctx` (default 4096),
  `briefing_reasoning_effort`, `briefing_max_tokens` (16384),
  `briefing_num_ctx` (8192).
- **`backend/routes/settings.py`** — exposes/validates all of the above;
  reasoning effort must be in the accepted set, tokens clamp to `[256, 65536]`,
  `num_ctx` clamps to `[512, 131072]`.
- **`backend/routes/chat.py`** — `/briefing/run` maps `EmptyCompletion` → 502.

### Frontend

- **Model & VRAM** (default chat model) and **Overnight Briefing** each gained
  **Thinking effort** (dropdown), **Output token limit**, and **Context window**
  inputs, with notes that thinking needs a bigger `num_ctx`.

## Gotchas / recommendations for future us

- **To use thinking on the briefing:** set Thinking effort = low/medium **and**
  raise the briefing Context window to ~16384+. Thinking is verbose (~2400
  tokens even at "low"); the 8192 default can be tight with a large journal.
- **Bigger `num_ctx` costs VRAM** — relevant on the 8 GB budget. It's a knob, not
  a free lunch.
- **`num_predict` (output limit) ≠ `num_ctx` (context window).** They fail
  differently: small `num_predict` truncates output; small `num_ctx` + thinking
  empties it. `done=length` can mean either.
- **JSON grammar + thinking = `{}`.** If you ever add a new structured call that
  wants reasoning, drop `format=json` and lean on `_parse_json_response`.
- **The `/v1` shim can't set `num_ctx`.** Anything needing it must use native
  `/api/chat`.
- **Docs drift:** `CLAUDE.md` lists the `ollama` Python SDK as a dependency, but
  it isn't installed. We used `requests` against the native endpoint instead.
  (Worth reconciling the doc.)

## Key empirical results (for reference)

| Setup                                               | content                     | thinking | done   | verdict          |
| --------------------------------------------------- | --------------------------- | -------- | ------ | ---------------- |
| 26B, `format=json`, think=none, ctx 8192            | 354                         | 0        | stop   | ✅ works         |
| 26B, `format=json`, think=low, ctx 8192             | 2 (`{}`)                    | 1210     | stop   | ❌ grammar+think |
| 26B, no format, think=low, ctx 4096 (real prompt)   | 0                           | 9444     | length | ❌ ctx too small |
| 26B, no format, think=low, ctx 16384 (real prompt)  | 1228                        | 11384    | stop   | ✅ works         |
| 26B, native path, think=low, ctx 16384 (end-to-end) | 407-char briefing + 3 todos | —        | —      | ✅ shipped       |
