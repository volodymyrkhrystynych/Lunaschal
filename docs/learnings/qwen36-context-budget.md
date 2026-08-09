# Why Qwen3.6 buys twice the context on the same card

_2026-08-09. Supersedes the context numbers in [local-model-context-budget.md](local-model-context-budget.md), which remain correct about Gemma 4 26B A4B._

The chat model moved from Gemma 4 26B A4B to Qwen3.6 35B A3B for one reason: context. On the same RTX 3070 (7.8 GB, ~1.7 GB of it already holding the desktop), Gemma topped out near 90k tokens and failed to load at 130k. Qwen3.6 reaches 190k in the same VRAM.

The gain is architectural, not a quantization trick. Both models are quantized the same way, and both use a q8_0 KV cache.

## The arithmetic

Only some layers in either model keep a cache that grows with context. What matters is how many, and how wide each one is.

|                                       | Gemma 4 26B A4B                  | Qwen3.6 35B A3B                       |
| ------------------------------------- | -------------------------------- | ------------------------------------- |
| Layers                                | 30                               | 40                                    |
| Layers whose cache grows with context | 5 full-attention (every 6th)     | 10 full-attention (every 4th)         |
| The rest                              | 25 × sliding-window, 1024 tokens | 30 × Gated DeltaNet                   |
| KV heads × head_dim on those layers   | 8 × 256                          | **2 × 256**                           |
| Values per token (K+V)                | 5×8×256×2 = 20,480               | 10×2×256×2 = **10,240**               |
| **q8_0 bytes per token**              | **21.25 KiB**                    | **10.6 KiB**                          |
| Context-independent cost              | ~111 MB (the sliding windows)    | ~63 MB per sequence (recurrent state) |

Qwen has _twice_ as many full-attention layers, but a quarter of the KV heads on each. Four times fewer heads over twice the layers is the entire factor of two. Nothing about the quantization changed.

Both models are hybrids — most layers are cheap by design. The difference is what "cheap" means: Gemma's 25 sliding-window layers still hold a real 1024-token cache each, while Qwen's 30 DeltaNet layers hold a fixed-size recurrent state that does not grow with the conversation at all.

q8_0 is 34 bytes per 32 values (1.0625 B/value), not 1.0 — the tables below use the real figure.

## Checking it against this box

The formula predicts **1.18 GB** for the old `[gemma4]` preset (49,152 tokens across two slots, q8_0). [presets.ini](../../llama/presets.ini) recorded the measured figure as "about 1.2 GB". That agreement is the only reason to trust the projections below rather than measure every one.

| Context     | Gemma KV (q8_0)                       | Qwen3.6 KV (q8_0)          | Qwen3.6 KV (q4_0) |
| ----------- | ------------------------------------- | -------------------------- | ----------------- |
| 90,000      | **2.07 GB** ← Gemma's comfortable max | 1.04 GB                    | 0.58 GB           |
| 131,072     | 2.96 GB ← Gemma failed to load        | 1.49 GB                    | 0.82 GB           |
| **190,000** | 4.05 GB                               | **2.07 GB** ← what shipped | 1.15 GB           |
| 262,144     | 5.56 GB                               | 2.91 GB                    | 1.57 GB           |

190,000 was chosen because it lands on the KV budget that was already _measured_ to load comfortably. 262,144 (the model's native maximum) needs 2.91 GB — the budget that made Gemma fail — and is probably still reachable, because Qwen's GPU-resident weights are smaller (hidden 2048 vs 2816, and a 151k vocabulary against Gemma's 262k, worth roughly 400M fewer embedding parameters). It was not taken on the first pass: the point of the swap was headroom, and spending all of it immediately would leave nothing for lowering `n-cpu-moe`, which is the speed knob.

## What it actually measured

First live load, 2026-08-09, RTX 3070 with the desktop already holding ~1.2 GB:

|                                           |                                                                                                   |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `n_ctx` reported                          | 190,208 (`n_ctx_train` 262,144)                                                                   |
| `n_slots` / `kv_unified`                  | 2 / `true` — **`n_ctx_slot = 190208`**, i.e. each slot may address the whole pool, not a 95k half |
| llama-server VRAM, `qwen36` alone         | **5,732 MiB**, leaving 878 MiB free                                                               |
| VRAM with `gemma4-12b-omni` also resident | 7,323 MiB used, 509 MiB free — the CPU-only preset still costs ~370 MiB of CUDA context           |
| Load time                                 | 17.9 s                                                                                            |
| Generation                                | **33.7 tok/s** (Gemma 4 26B A4B measured 25)                                                      |
| Model size / params                       | 22,349,466,112 bytes / 34.66B                                                                     |

**`n-cpu-moe` stays at 40, and that is now measured rather than cautious.** Each layer's routed experts are ~0.48 GB at Q4, so pulling even one back onto the GPU needs ~480 MiB of the 878 MiB that `qwen36` leaves — and the omni preset's CUDA context wants ~370 MiB of that. There is no room to trade context for expert residency at 190k. The trade was made in the other direction on purpose, and generation is faster than the old model anyway.

Two load-time warnings, neither fatal:

- `common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 999, abort` — llama.cpp declining to auto-size because the preset is explicit. Intended.
- `tensor overrides to CPU are used with mmap enabled - consider using --no-mmap for better performance` — worth benchmarking, but `--no-mmap` means the 22 GB is read into anonymous memory rather than page cache, so it interacts with having the 7.4 GB omni model resident too. Not changed without measuring both.

## Three things that will bite

**The DeltaNet state is f32 and `cache-type-*` does not touch it.** Per layer it is `32 v-heads × 128 × 128` for the recurrent state plus a small convolution state — about 2.1 MB, so ~63 MB across the 30 layers, per sequence. With `parallel = 2` that is a flat ~126 MB before a single token is generated. It does not scale with context, which is the good news, but it also cannot be quantized away.

**`--ctx-size` divides equally among slots, so `--kv-unified` is doing real work.** llama.cpp has no per-slot context size. Without `kv-unified`, `parallel = 2` hard-partitions 190k into 95k + 95k, and a long conversation hits a wall while the second slot sits empty. Unified makes it one shared pool: a chat can grow toward the whole 190k, and a background structured call takes only the few thousand tokens it actually needs. The `parallel = 2` still matters — it is what stops a background job queueing behind an interactive message.

**`n-cpu-moe` does not transfer between models.** The measured 28 was 28 _of Gemma's 30 layers_. Qwen has 40, and its experts are shaped differently (256 experts of intermediate 512, 8 active, against Gemma's 128). The preset ships at 40 — every routed expert in system RAM, the configuration certain to load — and wants walking down with `llama-bench`. One published run on the same 8 GB card found the _opposite_ trend from 12 GB cards: pushing more experts to the CPU was about 5× faster on prompt processing, with a sharp threshold. Re-measure; do not assume the shape of the curve.

## Why the weights stayed at Q4

Q8_0 weights (~37 GB) were considered and rejected on three counts, in ascending order of importance:

1. **Quality gain is the smallest term.** UD-Q4_K_XL is Unsloth Dynamic — attention, embeddings and the router already sit at higher precision, and only the routed experts are quantized hard. It sits on the Pareto frontier for KL divergence. Q4→Q8 on an MoE like this is well under a point on benchmarks.
2. **Generation roughly halves.** Per token the model reads ~1.0B expert parameters from system RAM: ~0.59 GB at Q4_K_XL, ~1.07 GB at Q8_0. On this box (Ryzen 7 5800X, dual-channel DDR4, ~40 GB/s achievable) that is a ceiling of ~68 tok/s against ~37 tok/s. Real numbers land below the ceiling but the ratio holds, because the work is purely bandwidth-bound.
3. **It would eat the context it was bought for.** `n-cpu-moe` only moves _expert_ tensors to RAM; attention, DeltaNet projections, router, norms and embeddings stay GPU-resident regardless — roughly 1.5–1.8B parameters. Q8 adds ~0.9 GB there, which at 10.6 KiB/token is **~85,000 tokens of context**. Q8 weights would have taken the model back to roughly Gemma's context, which is the thing the swap existed to fix.

Q5_K_XL (~26.6 GB) is the sane middle if more quality is ever wanted: ~1.25× the bytes per token instead of ~1.8×, and ~0.2 GB more GPU-resident.

## Sources

Derived from the models' own `config.json` files rather than from any write-up: [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) (40 layers, `full_attention_interval` 4, `num_key_value_heads` 2, `head_dim` 256) and [google/gemma-4-26b-a4b-it](https://huggingface.co/google/gemma-4-26b-a4b-it) (30 layers, 8 KV heads, `sliding_window` 1024). Published VRAM tables for this model disagree with each other by an order of magnitude; the config plus the one measured data point from this machine was more reliable than any of them.
