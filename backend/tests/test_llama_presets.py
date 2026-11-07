"""llama/presets.ini is not code, but a wrong line in it costs VRAM silently."""

import configparser
from pathlib import Path

PRESETS = Path(__file__).resolve().parents[2] / 'llama' / 'presets.ini'


def _presets():
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(';',))
    parser.read_string('[top]\n' + PRESETS.read_text())
    return {
        name: dict(parser[name])
        for name in parser.sections()
        if name not in ('top', '*')
    }


def test_cpu_only_presets_do_not_open_the_gpu():
    """n-gpu-layers = 0 alone still spends 372 MiB of [qwen36]'s margin."""
    cpu_only = {
        name: options
        for name, options in _presets().items()
        if options.get('n-gpu-layers') == '0'
    }
    assert cpu_only, 'expected at least one CPU-only preset'
    missing = [name for name, options in cpu_only.items() if options.get('device') != 'none']
    assert missing == []


def test_chat_preset_stays_on_the_gpu():
    options = _presets()['qwen36']
    assert options['n-gpu-layers'] == '999'
    assert 'device' not in options
