"""
Preview any ElevenLabs voice ID with a Hindi test sentence.
Usage:
    .\.venv\Scripts\python.exe preview_el_voice.py <voice_id>
    .\.venv\Scripts\python.exe preview_el_voice.py <voice_id> "custom text here"
"""

import sys
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
import numpy as np
import sounddevice as sd

API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: ELEVENLABS_API_KEY not set in .env")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python preview_el_voice.py <voice_id> [text]")
    sys.exit(1)

voice_id = sys.argv[1].strip()
text = sys.argv[2] if len(sys.argv) > 2 else (
    "नमस्ते! मैं जार्विस हूँ। आपकी कैसे मदद कर सकती हूँ?"
)

print(f"Playing voice: {voice_id}")
try:
    print(f"Text: {text}\n")
except UnicodeEncodeError:
    print("Text: [Hindi — see script source]\n")

resp = requests.post(
    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
    headers={
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/octet-stream",
    },
    params={"output_format": "pcm_24000"},
    json={
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.80,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    },
    stream=True,
    timeout=30,
)

if resp.status_code != 200:
    print(f"ERROR {resp.status_code}: {resp.text}")
    sys.exit(1)

pcm_data = b""
for chunk in resp.iter_content(chunk_size=4096):
    if chunk:
        pcm_data += chunk

if not pcm_data:
    print("No audio received.")
    sys.exit(1)

audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
sd.play(audio, samplerate=24000)
sd.wait()
print(f"Done. If this sounds good, add to .env:")
print(f"  ELEVENLABS_HINDI_VOICE_ID={voice_id}")
