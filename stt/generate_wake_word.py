#!/usr/bin/env python3
"""
Generate a 'hey Luna' wake word model using openwakeword's automated training pipeline.

Usage:
    python stt/generate_wake_word.py

Output:
    stt/models/hey_luna.onnx

Then set the env var when running the listener:
    WAKE_WORD_MODEL=stt/models/hey_luna.onnx ./stt/run_listener.sh
"""

import os
import sys
import pathlib

OUTPUT_DIR   = pathlib.Path(__file__).parent / "models"
OUTPUT_MODEL = OUTPUT_DIR / "hey_luna.onnx"
WAKE_PHRASE  = "hey luna"

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    try:
        import openwakeword  # noqa: F401
    except ImportError:
        print("openwakeword not installed. Run: bash stt/setup.sh")
        sys.exit(1)

    try:
        from openwakeword.train import train_model  # type: ignore
    except ImportError:
        print(
            "openwakeword.train not available in this version.\n"
            "Try: pip install openwakeword[train]\n"
            "Or see: https://github.com/dscripka/openWakeWord/blob/main/docs/custom_models.md"
        )
        sys.exit(1)

    print(f'Generating wake word model for "{WAKE_PHRASE}" ...')
    print(f"Output: {OUTPUT_MODEL}\n")
    print("This may take several minutes on first run (downloads TTS models).\n")

    train_model(
        target_phrase=WAKE_PHRASE,
        output_dir=str(OUTPUT_DIR),
        model_name="hey_luna",
        n_samples=5000,
        epochs=30,
    )

    if OUTPUT_MODEL.exists():
        print(f"\nDone! Model saved to: {OUTPUT_MODEL}")
        print("\nStart the listener with wake word support:")
        print(f'  WAKE_WORD_MODEL={OUTPUT_MODEL} ./stt/run_listener.sh')
    else:
        print("\nModel generation may have completed — check", OUTPUT_DIR)


if __name__ == "__main__":
    main()
