#!/usr/bin/env python3
"""
List evdev key codes for use in STT_PASTE_KEY / STT_VOICE_KEY.

Usage:
  python3 stt/keycodes.py            # print common key codes
  python3 stt/keycodes.py --detect   # press any key to see its code
  python3 stt/keycodes.py --all      # dump every KEY_* constant
"""

import sys

COMMON = [
    # Function keys
    "KEY_F1", "KEY_F2", "KEY_F3", "KEY_F4", "KEY_F5", "KEY_F6",
    "KEY_F7", "KEY_F8", "KEY_F9", "KEY_F10", "KEY_F11", "KEY_F12",
    # Modifiers
    "KEY_LEFTCTRL", "KEY_RIGHTCTRL",
    "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
    "KEY_LEFTALT", "KEY_RIGHTALT",
    "KEY_LEFTMETA", "KEY_RIGHTMETA",
    "KEY_CAPSLOCK", "KEY_SCROLLLOCK", "KEY_NUMLOCK",
    # Navigation
    "KEY_INSERT", "KEY_DELETE", "KEY_HOME", "KEY_END",
    "KEY_PAGEUP", "KEY_PAGEDOWN",
    "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT",
    # Extra / media
    "KEY_PAUSE", "KEY_SYSRQ", "KEY_ESC",
    "KEY_MUTE", "KEY_VOLUMEDOWN", "KEY_VOLUMEUP",
    "KEY_PLAYPAUSE", "KEY_NEXTSONG", "KEY_PREVIOUSSONG",
    "KEY_PRINT",
]


def list_common():
    from evdev import ecodes
    print("Common key codes (name → numeric value)")
    print("-" * 40)
    for name in COMMON:
        val = getattr(ecodes, name, None)
        mark = "" if val is not None else "  ← NOT on this kernel"
        print(f"  {name:<22} {val}{mark}")
    print()
    print("Set via env var, e.g.:  STT_PASTE_KEY=KEY_F2  STT_VOICE_KEY=KEY_RIGHTALT")


def list_all():
    from evdev import ecodes
    names = sorted(k for k in dir(ecodes) if k.startswith("KEY_"))
    for name in names:
        print(f"  {name:<30} {getattr(ecodes, name)}")


def detect():
    import evdev

    devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    keyboards = [d for d in devices if evdev.ecodes.KEY_A in d.capabilities().get(evdev.ecodes.EV_KEY, [])]
    if not keyboards:
        sys.exit("No keyboard devices found (try running with sudo).")

    print("Listening on:")
    for d in keyboards:
        print(f"  {d.path}  {d.name}")
    print("\nPress any key (Ctrl-C to quit)…\n")

    import asyncio
    from evdev import categorize, KeyEvent

    async def read_events():
        async def watch(dev):
            async for event in dev.async_read_loop():
                if event.type == evdev.ecodes.EV_KEY:
                    ke = categorize(event)
                    if ke.keystate == KeyEvent.key_down:
                        name = ke.keycode if isinstance(ke.keycode, str) else ke.keycode[0]
                        print(f"  keycode = {name:<22}  scancode = {ke.scancode}")
        await asyncio.gather(*(watch(d) for d in keyboards))

    try:
        asyncio.run(read_events())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if "--detect" in sys.argv:
        detect()
    elif "--all" in sys.argv:
        list_all()
    else:
        list_common()
