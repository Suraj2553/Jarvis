"""
Run this to browse ElevenLabs voices available to your account.
Usage:
    .\.venv\Scripts\python.exe list_el_voices.py
    .\.venv\Scripts\python.exe list_el_voices.py hindi
    .\.venv\Scripts\python.exe list_el_voices.py female
"""

import sys
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: ELEVENLABS_API_KEY not set in .env")
    sys.exit(1)

filter_term = sys.argv[1].lower() if len(sys.argv) > 1 else ""

resp = requests.get(
    "https://api.elevenlabs.io/v1/voices",
    headers={"xi-api-key": API_KEY},
    timeout=15,
)
if resp.status_code == 401:
    print("ERROR 401: API key rejected. Your key may have expired.")
    print(f"Key used (masked): {API_KEY[:8]}…{API_KEY[-4:]}")
    print("\nGet a fresh key at: elevenlabs.io > Profile > API Keys")
    print("Update ELEVENLABS_API_KEY in your .env file then re-run.")
    sys.exit(1)
resp.raise_for_status()
voices = resp.json().get("voices", [])

# Also fetch shared/library voices the account has added
print(f"\n{'='*65}")
print(f"  ElevenLabs voices on your account  ({len(voices)} total)")
if filter_term:
    print(f"  Filter: '{filter_term}'")
print(f"{'='*65}\n")

fmt = "{:<30} {:<32} {}"
print(fmt.format("NAME", "VOICE ID", "LABELS"))
print("-" * 80)

shown = 0
for v in sorted(voices, key=lambda x: x.get("name", "")):
    name   = v.get("name", "")
    vid    = v.get("voice_id", "")
    labels = v.get("labels", {})
    label_str = "  ".join(f"{k}:{val}" for k, val in labels.items())

    searchable = (name + label_str).lower()
    if filter_term and filter_term not in searchable:
        continue

    print(fmt.format(name[:29], vid, label_str[:50]))
    shown += 1

print(f"\n{shown} voice(s) shown.")
print("\nTo use a voice for Hindi, add to your .env:")
print('  ELEVENLABS_HINDI_VOICE_ID=<voice_id_here>')
print("\nThen preview a voice with:")
print('  .venv\\Scripts\\python.exe preview_el_voice.py <voice_id>')
