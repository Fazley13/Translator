"""Check which translation services work on your network.

Run:  python diagnose.py
Paste the whole output if translation fails and you need help.
"""
import sys
import time

import translator_service as service

print(f"Python {sys.version.split()[0]} at {sys.executable}\n")

for name, provider in service._PROVIDERS.items():
    label = service.LABELS[name]
    started = time.monotonic()
    try:
        result = provider("Hello, how are you?", "en", "bn")
        outcome = f"OK -> {result}"
    except service.TranslationError as exc:
        outcome = f"FAILED ({exc.detail or exc})"
    except Exception as exc:  # noqa: BLE001
        outcome = f"FAILED ({exc.__class__.__name__}: {exc})"
    print(f"{label:20} {time.monotonic() - started:5.1f}s  {outcome}")

print(
    "\nAt least one line should say OK. If every line fails with HTTP 429 or 'no connection', "
    "your network is blocking these services: try a phone hotspot or turn off any VPN/proxy."
)
