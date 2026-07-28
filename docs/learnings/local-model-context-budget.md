# Sizing num_ctx for a local model (and why parameter count doesn't tell you)

`num_ctx` is the one generation setting that costs VRAM before a single token is
generated: Ollama allocates the KV cache for the **whole** window when it loads
the model. `num_predict` is just a stop condition and reserves nothing.

The KV cost per token is set by the model's attention shape, not its size, and
the spread between architectures is large enough that a rule of thumb copied
from one model is actively wrong on the next.

## Read the shape off the GGUF

Ollama doesn't need to be running — the metadata is in the blob:

```bash
# manifest -> layer digests; the "model" layer is the GGUF
cat ~/.ollama/models/manifests/registry.ollama.ai/library/<model>/<tag>
# then parse the GGUF KV header from ~/.ollama/models/blobs/sha256-<digest>
```

The keys that matter: `*.block_count`, `*.attention.head_count_kv`,
`*.attention.key_length`, `*.attention.value_length`, `*.context_length`.

Per token, at f16:

```
bytes/token = (key_length + value_length) x kv_heads x 2 x (layers with KV)
```

`head_count_kv` may be an **array**, one entry per block. Zeros mean that layer
keeps no KV cache at all — a hybrid model where only some layers are full
attention and the rest are recurrent/SSM with a small constant state.

## Worked example: qwen3.6:35b

```
block_count            40
full_attention_interval 4
head_count_kv          [0,0,0,2, 0,0,0,2, ...]   -> 10 layers x 2 heads
key_length/value_length 256 / 256
context_length         262144
```

`(256+256) x 2 x 2 x 10` = **20 KB/token**, so 32K context is ~640 MiB and the
full 128K is ~2.5 GiB. Cheap for the context length, because 30 of the 40 layers
are SSM. A dense model of similar parameter count with KV on every layer costs
several times that per token.

## The knobs that interact

- **Keep the briefing's `num_ctx` equal to the chat model's.** They share a model
  by default, and changing `num_ctx` between requests forces Ollama to
  re-allocate and reload — tens of GB for a large local model, once at briefing
  time and again on the user's next message.
- **`num_predict` has to be reachable inside `_NATIVE_TIMEOUT`** (1800 s). A model
  served mostly from system RAM runs at roughly 10-15 tok/s, so a ceiling in the
  tens of thousands of tokens can't complete — a runaway generation becomes a
  lost reply instead of a truncated one.
- **Don't set sampler options.** `_native_body` deliberately sends only `num_ctx`
  and `num_predict`; temperature/top_p/top_k/min_p/presence_penalty come from the
  model's own baked-in params (`application/vnd.ollama.image.params` in the
  manifest), which are the values the model's authors recommend. Overriding them
  in the app would silently replace a per-model tuning with a global one.
- **Reasoning costs latency on the interactive path.** `_native_chat_stream` does
  not yield the `thinking` field, so every thinking token is time the user spends
  looking at "Thinking...". On hardware where the model is CPU-resident, prefer
  `none` for chat and spend the reasoning budget overnight in the briefing.
