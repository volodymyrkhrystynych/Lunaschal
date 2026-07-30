# Sizing the context window for a local model (and why parameter count doesn't tell you)

The context window is the one setting that costs VRAM before a single token is
generated: the KV cache is allocated for the **whole** window when the model
loads. The output ceiling (`max_tokens`) is just a stop condition and reserves
nothing.

Since the move to llama.cpp, the window is **not an app setting at all** — it's
`ctx-size` in [`llama/presets.ini`](../../llama/presets.ini), fixed when
llama-server loads the model, and changing it means restarting llama-server.
That's strictly simpler than the Ollama arrangement it replaced, where `num_ctx`
was per-request and any mismatch between two requests silently evicted and
reloaded the model. (Two of the app's own settings, `llm_num_ctx` and
`briefing_num_ctx`, are retired columns kept only so the migration stays an
append-only ALTER.)

What has _not_ changed is how you size it.

## The KV cost per token is set by the attention shape, not the model size

The spread between architectures is large enough that a rule of thumb copied from
one model is actively wrong on the next. Read the shape off the GGUF:

```bash
# llama.cpp prints the KV header when it loads a model:
llama-server -m model.gguf --verbose 2>&1 | grep -E "block_count|head_count_kv|key_length|value_length|context_length"
```

The keys that matter: `*.block_count`, `*.attention.head_count_kv`,
`*.attention.key_length`, `*.attention.value_length`, `*.context_length`.

Per token, at f16:

```
bytes/token = (key_length + value_length) x kv_heads x 2 x (layers with KV)
```

Two things make "layers with KV" smaller than `block_count`:

- **`head_count_kv` may be an array**, one entry per block. Zeros mean that layer
  keeps no KV cache at all — a hybrid model where only some layers are full
  attention and the rest are recurrent/SSM with a small constant state.
- **Sliding-window layers don't scale with context.** llama.cpp sizes them to the
  window, so only the global-attention layers grow with `ctx-size` — unless you
  pass `--swa-full`, which you should not. See
  [moe-expert-placement.md](moe-expert-placement.md).

## Worked example: gemma-4-26B-A4B (current)

```
block_count             30
head_count_kv           8
key_length/value_length 256 / 256
sliding_window          1024
layer pattern           5 sliding : 1 global, repeating
context_length          262144
```

`(256+256) x 8 x 2` = **8 KB/token/layer**, but split by layer type:

- 25 sliding layers: `25 x 1024 x 8 KB` ≈ 204 MB **per sequence, fixed**
- 5 global layers: `5 x ctx x 8 KB` — 1.3 GB at 32K, 2.0 GB at 48K, 5.4 GB at 128K

So 48K costs ~2.2 GB at f16, ~1.2 GB with `cache-type-k/v = q8_0`. A dense model
with KV on all 30 layers would cost six times that — which is why a 26B model can
hold a 48K window on an 8 GB card at all.

## Worked example: qwen3.6:35b (the previous model)

```
block_count             40
full_attention_interval 4
head_count_kv           [0,0,0,2, 0,0,0,2, ...]   -> 10 layers x 2 heads
key_length/value_length 256 / 256
context_length          262144
```

`(256+256) x 2 x 2 x 10` = **20 KB/token**, so 32K is ~640 MiB and the full 128K
~2.5 GiB. Cheap for the context length, because 30 of the 40 layers are SSM. Kept
here as the contrast: same vendor-quoted 256K window, completely different cost
curve.

## The knobs that interact

- **Lower `ctx-size` before moving experts to the CPU.** Context costs no quality;
  offloading experts costs throughput.
- **`max_tokens` has to be reachable inside the client timeout** (`_TIMEOUT`,
  1800 s in `backend/ai/llm.py`). Gemma 4 with CPU-resident experts measures
  25 tok/s, so a ceiling in the tens of thousands of tokens cannot complete — a
  runaway generation becomes a lost reply instead of a capped one.
- **Sampler settings now live in the preset, not the app.** Ollama baked
  per-model params into its manifest (`application/vnd.ollama.image.params`), so
  the app deliberately sent none. llama.cpp has no equivalent: unset means
  llama.cpp's generic defaults, not the model author's. Gemma 4's published
  recommendation (temp 1.0 / top-p 0.95 / top-k 64) is therefore set explicitly
  in `llama/presets.ini` — the right layer for it, since it's a property of the
  model rather than of a feature.
- **Slots divide the window.** `ctx-size` is the total; with `parallel = 2` each
  request gets half. The app runs two slots so a background structured call can't
  block an interactive chat message behind it.
- **Thinking costs latency on the interactive path, and the number is brutal.**
  `chat_stream_deltas` does not yield thinking deltas, so every thinking token is
  time the user spends looking at "Thinking...". Measured on this machine, same
  trivial prompt ("Say hello in exactly three words"), same warm model:

  | `llm_thinking` | first token | total  |
  | -------------- | ----------- | ------ |
  | on             | **25.5 s**  | 25.7 s |
  | off            | **1.3 s**   | 1.5 s  |

  Twenty times. Gemma 4's channel is unbounded — unlike the graded Ollama levels,
  which capped it — so there is no "a little thinking" setting to reach for. Chat
  defaults off and the Ollama→llama.cpp migration force-resets it off for exactly
  this reason (`_ensure_llama_server_settings`); the overnight briefing has its own
  toggle, where the tokens are free.
