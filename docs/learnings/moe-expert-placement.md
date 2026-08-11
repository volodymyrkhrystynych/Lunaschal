# Placing MoE expert tensors: why `--n-cpu-moe` beats whole-layer offload

> **The method holds; the numbers are Gemma's.** The chat model is now
> Qwen3.6 35B A3B (40 layers, 256 experts), so every measured `n-cpu-moe` value
> below applies to a model the router no longer loads — see
> [qwen36-context-budget.md](qwen36-context-budget.md). Re-measure rather than
> translate: one published run on this same card found the speed curve sloping
> the _opposite_ way for this model.

The machine has an RTX 3070 (8 GB, ~7.8 GB usable) and 62 GB of system RAM.
Gemma 4 26B A4B is ~17 GB at Q4_K_XL. It cannot be VRAM-resident, so the only
question that matters for speed is **which tensors go where** — and for a
Mixture-of-Experts model the answer is not "as many whole layers as fit".

## The two placements

Gemma 4 26B A4B is 30 layers, hidden 2816, 16 heads / 8 KV heads, head_dim 256,
with 128 routed experts per layer (top-8) plus 1 shared expert. That splits into:

| Group                                      | Params  | Per layer @ Q4 | Read per token       |
| ------------------------------------------ | ------- | -------------- | -------------------- |
| Routed experts                             | ~22.8 B | ~457 MB        | only 8/128 ≈ 28.5 MB |
| Attention + shared expert + router + norms | ~1.6 B  | ~32 MB         | all of it            |
| Embeddings / output (262144 vocab)         | ~0.74 B | —              | lookup only          |

The routed experts are **90% of the weights but 6% of the per-token reads.**
That asymmetry is the whole game.

**Ollama places whole layers**, and it reports this itself. The runner we replaced
was serving the previous model like so:

```
$ ollama ps
NAME           ID              SIZE     PROCESSOR          CONTEXT
qwen3.6:35b    07d35212591f    24 GB    82%/18% CPU/GPU    32768
```

**18% on the GPU.** The other 82% — attention, KV cache and experts together — was
on the CPU, using 5544 MiB of VRAM to achieve it. Per token those CPU-resident
layers read the dense weights _and_ the active experts, and their attention runs
on the CPU too. Ollama has no MoE-aware knob: `ollama/ollama#11772` is still an
open feature request.

**llama.cpp can split by tensor.** `--n-cpu-moe N` keeps the routed experts of
the first N layers in system RAM while attention, KV cache, router, shared expert
and norms for **every** layer stay in VRAM. At the value this machine actually
ships (`-ncmoe 28`, measured below), per-token CPU reads are `28 x 28.5 MB` ≈
**0.80 GB**, and all 30 layers' attention runs on the GPU.

So: ~1.6x less memory traffic for generation, and a much larger factor on
prompt processing, where CPU-side attention over a long prompt was the real cost.
That, not Ollama's ~3-10% Go wrapper overhead, is where the "Ollama gives you
half the tok/s" reports come from on hardware like this.

`-ot` / `--override-tensor` does the same thing with regexes (`-ot "exps=CPU"`)
and is what you need for asymmetric multi-GPU setups. `--n-cpu-moe` is the same
idea with the common case pre-baked. Note the shared expert is named `*_shexp`,
not `*_exps`, so an `exps=CPU` regex correctly leaves it on the GPU.

## KV cache: read the attention shape, not the parameter count

Per token, per layer, at f16:

```
bytes/token = (key_length + value_length) x kv_heads x 2
            = (256 + 256) x 8 x 2 = 8 KB
```

But Gemma 4 interleaves **5 sliding-window layers (window 1024) to 1 global
layer**, repeating. llama.cpp sizes the sliding layers to the window, not the
context, so only 5 of 30 layers scale with context length:

- 25 sliding layers: `25 x 1024 x 8 KB` ≈ **204 MB per sequence, fixed**
- 5 global layers at 48K total: `5 x 49152 x 8 KB` ≈ **2.0 GB** (f16)

which `-ctk q8_0 -ctv q8_0` roughly halves, for no quality difference worth
measuring. This is why a 26B model can hold a 48K window on an 8 GB card at all.

## Two traps that cost VRAM silently

- **`--swa-full` destroys this.** It gives all 30 layers a full-size cache: ~8 GB
  at 48K instead of ~1.2 GB. Never set it here.
- **`-hf` auto-loads the vision mmproj** (~1.1 GB of VRAM) even though image
  input isn't wired into the app. `llama/presets.ini` uses explicit `model =`
  paths and `mmproj-auto = false` for exactly this reason.

## Tuning method

`n-cpu-moe` is hardware- and desktop-specific, so it has to be measured, not
copied:

1. Check what the desktop is already holding: `nvidia-smi`. On this machine
   Hyprland + browsers + Electron sit at **~1.7 GB**, leaving ~6.1 GB — and it
   moves when you open tabs. Budget for the peak, not the idle.
2. `llama-bench` at several values (30 down to 24), recording prompt processing
   (`pp`) and token generation (`tg`) separately — but treat this as an upper
   bound only, and see the warning below about benchmark defaults.
3. The curve is **V-shaped**: fastest at the _smallest_ value that genuinely
   fits, catastrophic once VRAM is overcommitted (the driver starts spilling),
   and slowly declining as you push more experts to the CPU. So walk down from 30
   until VRAM is nearly full, then back off one.
4. Lower `ctx-size` before raising `n-cpu-moe` — context costs no quality.

## Measured on this machine (llama.cpp b10182, RTX 3070, 5800X)

`llama-bench`, `-fa on -ctk q8_0 -ctv q8_0 -t 8 -p 512 -n 64`, small context:

| `n-cpu-moe`             | prompt (t/s) | generation (t/s) |
| ----------------------- | ------------ | ---------------- |
| 30 (all experts on CPU) | 399.8        | 24.0             |
| 28                      | 423.0        | 25.2             |
| 27                      | 417.3        | 25.8             |
| 26                      | 451.8        | 26.8             |
| 25                      | 468.6        | 27.4             |
| 24                      | —            | CUDA OOM         |

Textbook V-curve: monotonically better as experts move onto the GPU, then a wall.

**But that sweep is misleading, and this is the important lesson.** llama-bench
used its small default context, so it only measured whether the _weights_ fit. The
real preset (48K over 2 slots, `-ub 2048`) has to fit the KV cache and the compute
buffers too — and there, 25 dies allocating a 767 MiB buffer:

| Config (48K, 2 slots, `ub 2048`)         | Result                                 |
| ---------------------------------------- | -------------------------------------- |
| `n-cpu-moe` 25, 26, 27                   | CUDA OOM on a 767 MiB compute buffer   |
| **`n-cpu-moe` 28**                       | **loads, 5604 MiB, ~870 MiB headroom** |
| `cpu-moe` @ 128K, 1 slot (`gemma4-long`) | loads, 5726 MiB                        |

So the shipped value is **28**, not the 25 a weights-only benchmark suggests —
roughly 8% slower generation than the theoretical optimum, in exchange for actually
booting. **Always fit-test with the real `ctx-size`, `parallel` and `ubatch-size`,
never with llama-bench defaults.**

Headroom is worth more than that last 8% here, because the desktop baseline is not
constant: 1719 MiB measured with browsers idle, and it climbs as tabs open. With
both `gemma4` and `embed` resident the total sits near 7.3 GB of 8.0 GB, so the
margin is real but thin.

### What the switch actually bought — and how confident each number is

Be careful quoting a speedup here, because the two sides were never benchmarked
under the same conditions:

| Setup                           | Generation | Provenance                                 |
| ------------------------------- | ---------- | ------------------------------------------ |
| Ollama + qwen3.6:35b (previous) | ~10 tok/s  | operator recollection, **not measured**    |
| Ollama + gemma-4-26B-A4B        | <10 tok/s  | operator recollection ("slower than qwen") |
| llama.cpp + gemma-4-26B-A4B     | **25.2**   | `llama-bench`, measured                    |

So roughly **2.5x on generation**, and more than that for Gemma 4 specifically —
but only the bottom row is a measurement. No Ollama benchmark was ever run: the
Ollama runner was stopped to free VRAM before the llama.cpp sweep, and by then
the comparison would also have confounded the engine change with the model change.

If a defensible number is ever needed, the clean experiment is cheap and needs no
new downloads: Ollama stores plain GGUF blobs under
`~/.ollama/models/blobs/`, so `llama-bench -m <that blob>` benchmarks _both_
engines on identical weights and isolates the placement win from the model swap.

## System RAM is the other constraint

The CPU-resident experts are ~13 GB and are read on every token. If the kernel
pages them out, throughput collapses. Keep the default `mmap` (so the kernel can
evict cleanly rather than swap-thrash) and check `free -h` has headroom —
this machine idles at ~48 GB used of 62 GB, which is closer than it looks.

## Follow-up worth trying: MTP speculative decoding

The unsloth repo also ships `mtp-gemma-4-26B-A4B-it-Q8_0.gguf`, a multi-token
prediction head for this exact model, and llama-server has `--spec-draft-*`
flags. A draft head that shares the base model's vocabulary is the cheapest
remaining speedup on the table for generation — untested here, but it is the next
thing to measure.
