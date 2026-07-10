#!/usr/bin/env python
"""Benchmark Whisper STT and Ollama LLM inference speed: GPU vs CPU.

Standalone script, no Flask app context needed. Run from the repo root with
the project venv:

    .venv/bin/python scripts/benchmark_inference.py
    .venv/bin/python scripts/benchmark_inference.py --stt-only --whisper-models small,turbo --runs 5
    .venv/bin/python scripts/benchmark_inference.py --llm-only --runs 5
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

import requests
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(__file__).resolve().parent / '.benchmark_cache'
SAMPLE_AUDIO = CACHE_DIR / 'sample.wav'

# Punctuation, numbers and a proper noun — the base Whisper model was observed
# to gloss over punctuation, so the sample needs enough of it to show up in RTF-vs-quality tradeoffs.
BENCHMARK_TEXT = (
    "Reminder: the quarterly review with Dr. Alvarez is on March 3rd at 2:30 PM, "
    "in conference room B. Please bring the Q1 report, the budget spreadsheet, "
    "and 15 printed copies of the summary. Also, don't forget to book the projector "
    "in advance -- it's first come, first served."
)
TTS_VOICE = 'af_heart'

OLLAMA_URL = 'http://localhost:11434'
LLM_MODEL = 'mistral:latest'
LLM_USER_MESSAGE = "Remind me to call the dentist tomorrow afternoon to reschedule my cleaning."

_KOKORO_MODEL = 'kokoro-v1.0.onnx'
_KOKORO_VOICES = 'voices-v1.0.bin'
_KOKORO_BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'
_TTS_CACHE = Path.home() / '.cache' / 'lunaschal' / 'tts'


def synth_audio(force: bool = False) -> Path:
    """Synthesize the fixed benchmark sentence with the app's own Kokoro TTS, so the
    STT benchmark is fully reproducible without recording anything."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if SAMPLE_AUDIO.exists() and not force:
        return SAMPLE_AUDIO

    from kokoro_onnx import Kokoro

    _TTS_CACHE.mkdir(parents=True, exist_ok=True)
    model_path = _TTS_CACHE / _KOKORO_MODEL
    voices_path = _TTS_CACHE / _KOKORO_VOICES
    for path, name in [(model_path, _KOKORO_MODEL), (voices_path, _KOKORO_VOICES)]:
        if not path.exists():
            print(f'Downloading {name}...')
            urllib.request.urlretrieve(f'{_KOKORO_BASE}/{name}', path)

    kokoro = Kokoro(str(model_path), str(voices_path))
    samples, sample_rate = kokoro.create(BENCHMARK_TEXT, voice=TTS_VOICE, speed=1.0, lang='en-us')
    sf.write(SAMPLE_AUDIO, samples, sample_rate)
    print(f'Synthesized benchmark audio -> {SAMPLE_AUDIO} ({len(samples) / sample_rate:.1f}s)')
    return SAMPLE_AUDIO


def _audio_duration_seconds(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def bench_stt(models: list[str], devices: list[str], audio_path: Path, runs: int) -> list[dict]:
    import gc
    import whisper

    duration = _audio_duration_seconds(audio_path)
    results = []
    for device in devices:
        for model_name in models:
            print(f'\n--- STT: {model_name} on {device} ---')
            t0 = time.perf_counter()
            try:
                model = whisper.load_model(model_name, device=device)
            except Exception as e:
                print(f'  FAILED to load: {e}')
                continue
            load_s = time.perf_counter() - t0

            model.transcribe(str(audio_path))  # warm-up: pay kernel-compile cost once, untimed

            timings = []
            text = ''
            for i in range(runs):
                t0 = time.perf_counter()
                result = model.transcribe(str(audio_path))
                timings.append(time.perf_counter() - t0)
                text = result['text']
                print(f'  run {i + 1}/{runs}: {timings[-1]:.2f}s')

            avg = statistics.mean(timings)
            results.append({
                'component': 'stt',
                'model': model_name,
                'device': device,
                'load_s': round(load_s, 2),
                'avg_s': round(avg, 3),
                'min_s': round(min(timings), 3),
                'max_s': round(max(timings), 3),
                'audio_duration_s': round(duration, 1),
                'realtime_factor': round(duration / avg, 2),
                'sample_text': text.strip()[:100],
            })

            del model
            gc.collect()
            if device == 'cuda':
                import torch
                torch.cuda.empty_cache()
    return results


def _ollama_generate(url: str, model: str, prompt: str, system: str, options: dict) -> dict:
    resp = requests.post(
        f'{url}/api/generate',
        json={
            'model': model,
            'prompt': prompt,
            'system': system,
            'stream': False,
            'format': 'json',
            'options': options,
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def bench_llm(runs: int, url: str, model: str) -> list[dict]:
    sys.path.insert(0, str(REPO_ROOT))
    from backend.ai.commands import COMMAND_PROMPT

    today = date.today()
    system = COMMAND_PROMPT.replace('{TODAY}', today.isoformat()).replace('{WEEKDAY}', today.strftime('%A'))

    configs = [('gpu', {}), ('cpu', {'num_gpu': 0})]
    results = []
    for label, options in configs:
        print(f'\n--- LLM: {model} on {label} ---')
        try:
            _ollama_generate(url, model, LLM_USER_MESSAGE, system, options)  # warm-up
        except requests.RequestException as e:
            print(f'  FAILED (is `ollama serve` running, and is {model} pulled?): {e}')
            continue

        timings = []
        stats = {}
        for i in range(runs):
            t0 = time.perf_counter()
            stats = _ollama_generate(url, model, LLM_USER_MESSAGE, system, options)
            timings.append(time.perf_counter() - t0)
            print(f'  run {i + 1}/{runs}: {timings[-1]:.2f}s')

        requests.post(f'{url}/api/generate', json={'model': model, 'keep_alive': 0}, timeout=30)

        eval_count = stats.get('eval_count', 0)
        eval_duration_s = stats.get('eval_duration', 0) / 1e9
        results.append({
            'component': 'llm',
            'model': model,
            'device': label,
            'avg_wall_s': round(statistics.mean(timings), 2),
            'min_wall_s': round(min(timings), 2),
            'max_wall_s': round(max(timings), 2),
            'eval_tokens_per_sec': round(eval_count / eval_duration_s, 1) if eval_duration_s else 0,
            'total_duration_s': round(stats.get('total_duration', 0) / 1e9, 2),
            'load_duration_s': round(stats.get('load_duration', 0) / 1e9, 2),
        })
    return results


def print_table(results: list[dict]) -> None:
    print('\n' + '=' * 72)
    print(f"{'component':<10}{'model':<16}{'device':<8}{'avg latency':<14}{'throughput':<20}")
    print('-' * 72)
    for r in results:
        if r['component'] == 'stt':
            avg, throughput = f"{r['avg_s']}s", f"RTF {r['realtime_factor']}x"
        else:
            avg, throughput = f"{r['avg_wall_s']}s", f"{r['eval_tokens_per_sec']} tok/s"
        print(f"{r['component']:<10}{r['model']:<16}{r['device']:<8}{avg:<14}{throughput:<20}")
    print('=' * 72)


def main():
    sys.stdout.reconfigure(line_buffering=True)  # keep prints in true order alongside whisper's stderr warnings
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--stt-only', action='store_true')
    parser.add_argument('--llm-only', action='store_true')
    parser.add_argument('--runs', type=int, default=3, help='timed runs per config (default: 3)')
    parser.add_argument('--whisper-models', default='small,turbo', help='comma-separated (default: small,turbo)')
    parser.add_argument('--audio', type=Path, default=None, help='use this WAV instead of synthesizing one')
    parser.add_argument('--regen-audio', action='store_true', help='force re-synthesis of the sample audio')
    parser.add_argument('--ollama-url', default=OLLAMA_URL)
    parser.add_argument('--llm-model', default=LLM_MODEL)
    args = parser.parse_args()

    results = []

    if not args.llm_only:
        audio_path = args.audio or synth_audio(force=args.regen_audio)
        models = [m.strip() for m in args.whisper_models.split(',') if m.strip()]
        results += bench_stt(models, ['cuda', 'cpu'], audio_path, args.runs)

    if not args.stt_only:
        results += bench_llm(args.runs, url=args.ollama_url, model=args.llm_model)

    print_table(results)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CACHE_DIR / f'results-{datetime.now():%Y%m%d-%H%M%S}.json'
    out_path.write_text(json.dumps(results, indent=2))
    print(f'\nResults written to {out_path}')


if __name__ == '__main__':
    main()
