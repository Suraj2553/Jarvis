# JARVIS v3.0 — Integration Guide

## Files to replace (drop-in, no main.py changes needed)

| v2 file | v3 file | Notes |
|---|---|---|
| `monitor.py` | `monitor.py` | Drop-in. ObservationLedger auto-initialises. |
| `personality/initiator.py` | `personality/initiator.py` | Drop-in. Imports ObservationLedger from monitor. |
| `audio/listener.py` | `audio/listener.py` | Drop-in. Loopback fix only. |
| `ui/war_room.py` | `ui/war_room.py` | Drop-in. Crash fix + rebuild. |
| `brain/llm_router.py` | `brain/llm_router.py` | Drop-in. Add new keys to config.json. |
| `config.py` | `config.py` | Drop-in. New keys merge gracefully with existing config.json. |

---

## New file to add

```
audio/conversation_state.py   ← new, add to project
```

---

## main.py changes (minimal — 3 additions)

### 1. Import ConversationState (near top, with other imports)
```python
from audio.conversation_state import ConversationState, reply_ends_with_question
```

### 2. Create the state machine (in JARVIS.__init__, after self._tts is created)
```python
self._conv_state = ConversationState(
    activate_fn=lambda: self._activate("question_followup"),
    config=self._cfg,
)
```

### 3. Wire it into the response handler

Find the method where JARVIS finishes speaking (after `self._speak(reply)` returns
or after the TTS `is_speaking()` loop ends).  Add:

```python
# After speaking a response:
if reply_ends_with_question(final_reply):
    self._conv_state.jarvis_asked_question()
else:
    self._conv_state.jarvis_done_speaking()
```

And at the top of `_activate()` (or wherever STT is triggered):
```python
def _activate(self, source: str = "user") -> None:
    if source != "question_followup" and not self._conv_state.can_activate():
        return   # already mid-conversation — don't double-activate
    ...
```

---

## Config.json — new keys to add

Open `%APPDATA%/JARVIS/config.json` and add (or let the new config.py
merge them automatically on next launch):

```json
{
  "gemini_api_key":   "YOUR_KEY_HERE",
  "nvidia_api_key":   "YOUR_KEY_HERE",
  "openrouter_api_key": "YOUR_KEY_HERE",
  "question_wait_timeout": 8.0,
  "llm_long_context_threshold": 3000
}
```

### Where to get free keys
| Provider | URL | Free tier |
|---|---|---|
| Google Gemini | https://aistudio.google.com/app/apikey | 15 req/min, 2M context |
| NVIDIA NIM | https://build.nvidia.com/explore/discover | $200 free credits |
| OpenRouter | https://openrouter.ai/keys | Many free-tier models |

---

## Dead code to delete (optional cleanup)

These files are superseded and no longer imported anywhere in the new architecture:

```
audio/voice_engine.py      ← Coqui TTS engine; root tts.py is what's actually used
```

The root `tts.py` / `stt.py` / `wake_word.py` are **still active** — do NOT delete them.
Only `audio/voice_engine.py` is dead.

---

## War Room — QWebEngine (for live map)

Install if not already present:
```
pip install PyQt6-WebEngine
```

Without it, the war room shows a text list of GDELT events instead of the map.
Everything else (news cards, system stats, markets, weather) works without it.

---

## Verification checklist after deploying

- [ ] Launch JARVIS — no `AttributeError: TextWordWrap` in logs
- [ ] CPU shown as 0-100% (not 600%) in war room system panel
- [ ] "It's midnight" said at most once per 2 hours across all sources
- [ ] Ask JARVIS a question — mic stays active for ~8s waiting for reply
- [ ] No `data discontinuity in recording` spam in jarvis_err.log
- [ ] War room map loads (if PyQt6-WebEngine installed)
- [ ] GDELT event dots appear on map within 30s of war room opening
