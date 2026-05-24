"""brain/brain.py — Upgraded JARVIS brain.

Groq: native OpenAI-format tool calling.
Ollama: ReAct JSON parsing (existing fallback method).
Response filter: strips forbidden sycophantic phrases before TTS.
Streaming: sentence-by-sentence delivery to TTS for near-zero latency.
"""

import json
import re
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from brain.llm_router import LLMRouter
from personality.conversation_engine import FillerSpeech, ResponseFilter

_filler_speech = FillerSpeech()
_response_filter = ResponseFilter()


# ------------------------------------------------------------------ #
#  Narration patterns — Phase 1C                                       #
#  Spoken as JARVIS begins each tool type so there's no silent gap.   #
# ------------------------------------------------------------------ #

NARRATION_PATTERNS: dict[str, list[str]] = {
    "get_weather":          ["Checking conditions now.", "Pulling up the weather.", "Querying the forecast."],
    "get_weather_forecast": ["Pulling the extended forecast.", "Checking the week ahead."],
    "get_news":             ["Scanning the headlines.", "Pulling the latest.", "Checking the feeds."],
    "web_search":           ["Searching that.", "On it."],
    "get_live_score":       ["Accessing the live feed.", "Checking the scoreboard."],
    "get_location":         [],
    "set_timer":            ["Setting that for you.", "Consider it done.", "Timer set."],
    "set_reminder":         ["Noted. I'll remind you.", "Reminder set."],
    "get_datetime":         [],  # answered inline, no narration needed
    "get_battery":          [],
    "calculate":            ["Running the numbers.", "Computing."],
    "open_app":             ["Opening that.", "On it."],
    "close_app":            ["Closing that.", "Done."],
    "take_screenshot":      ["Capturing the screen.", "Screenshot taken."],
    "read_screen":          ["Reading the screen."],
    "read_document_camera": ["Scanning the document.", "Analyzing with vision systems.", "Reading that for you."],
    "remember":             ["Noted.", "Storing that."],
    "recall":               ["Let me check.", "Searching memory."],
    "add_note":             ["Noted.", "Saved."],
    "read_notes":           ["Let me pull those up.", "Retrieving your notes."],
    "find_file":            ["Searching the disk.", "Looking for that."],
    "present_file":         ["Loading the presentation.", "Opening that for you."],
    "next_slide":           [],
    "prev_slide":           [],
    "draft_email":          ["Composing that.", "Opening Outlook."],
    "send_email":           ["Sending that.", "On it."],
    "read_emails":          ["Checking your inbox.", "Pulling up your emails."],
    "start_meeting":        ["Starting the meeting.", "Meeting room open."],
    "end_meeting":          ["Wrapping up.", "Generating meeting summary."],
    "start_focus_session":  ["Entering focus mode.", "Locking in."],
    "get_unread_count":     [],
    "get_stock_price":        ["Checking the markets.", "Pulling the price."],
    "translate_text":         ["Translating that.", "On it."],
    "get_morning_briefing":   ["Pulling up the world briefing.", "Fetching live data — weather, news, markets.", "One moment, sir."],
}

import random as _random


def _narrate_tool(speak_fn, tool_name: str) -> None:
    """Speak a narration phrase for the given tool, if one exists."""
    options = NARRATION_PATTERNS.get(tool_name, [])
    if options:
        speak_fn(_random.choice(options))


# ------------------------------------------------------------------ #
#  Tool definitions in OpenAI/Groq format                             #
# ------------------------------------------------------------------ #

GROQ_TOOLS = [
    {"type": "function", "function": {
        "name": "open_app",
        "description": "Opens an application by name",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string", "description": "App name"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "close_app",
        "description": "Closes/kills a running application by name",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Get current weather. Omit city to use the user's detected location.",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string",
                                               "description": "City name. Leave empty to use current location."}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "get_weather_forecast",
        "description": "Get multi-day weather forecast. Omit city to use the user's detected location.",
        "parameters": {"type": "object",
                       "properties": {
                           "city": {"type": "string",
                                    "description": "City name. Leave empty to use current location."},
                           "days": {"type": "integer", "description": "Number of days 1-7"}
                       },
                       "required": []}}},
    {"type": "function", "function": {
        "name": "get_news",
        "description": "Get latest news headlines on a topic",
        "parameters": {"type": "object",
                       "properties": {"topic": {"type": "string", "description": "News topic or 'world'"}},
                       "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for information",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_live_score",
        "description": "Get live cricket or sports match score",
        "parameters": {"type": "object",
                       "properties": {"match_query": {"type": "string"}},
                       "required": ["match_query"]}}},
    {"type": "function", "function": {
        "name": "get_location",
        "description": "Get the user's current detected location (city, region, country)",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_datetime",
        "description": "Get current date and time",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_battery",
        "description": "Get battery level and charging status",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_system_info",
        "description": "Get CPU, RAM, disk usage",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "take_screenshot",
        "description": "Take a screenshot of the screen",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_screen",
        "description": "OCR and read text currently visible on screen",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_document_camera",
        "description": "Read/OCR a document using camera or image file. Tasks: ocr, summarize, structured, translate.",
        "parameters": {"type": "object", "properties": {
            "task":       {"type": "string", "enum": ["ocr", "summarize", "structured", "translate"]},
            "image_path": {"type": "string", "description": "File path to image. Empty = capture from camera."},
        }}}},
    {"type": "function", "function": {
        "name": "type_text",
        "description": "Type text at the current cursor position",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "press_keys",
        "description": "Press keyboard shortcut (e.g. ctrl+c, alt+tab)",
        "parameters": {"type": "object",
                       "properties": {"combo": {"type": "string"}},
                       "required": ["combo"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a shell command",
        "parameters": {"type": "object",
                       "properties": {"cmd": {"type": "string"}},
                       "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "open_url",
        "description": "Open a URL in the default browser",
        "parameters": {"type": "object",
                       "properties": {"url": {"type": "string"}},
                       "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "Set system volume 0-100",
        "parameters": {"type": "object",
                       "properties": {"level": {"type": "integer"}},
                       "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "volume_up",
        "description": "Raise system volume",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "volume_down",
        "description": "Lower system volume",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "mute_toggle",
        "description": "Toggle system mute",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "play_pause_media",
        "description": "Play or pause media playback",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "next_track",
        "description": "Skip to next media track",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "prev_track",
        "description": "Go to previous media track",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_timer",
        "description": "Set a countdown timer",
        "parameters": {"type": "object",
                       "properties": {
                           "minutes": {"type": "number"},
                           "label": {"type": "string"}
                       },
                       "required": ["minutes"]}}},
    {"type": "function", "function": {
        "name": "set_reminder",
        "description": "Set a reminder for N minutes from now",
        "parameters": {"type": "object",
                       "properties": {
                           "message": {"type": "string"},
                           "minutes": {"type": "number"}
                       },
                       "required": ["message", "minutes"]}}},
    {"type": "function", "function": {
        "name": "add_note",
        "description": "Save a note",
        "parameters": {"type": "object",
                       "properties": {"content": {"type": "string"}},
                       "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "read_notes",
        "description": "Read saved notes",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Store a fact in long-term memory",
        "parameters": {"type": "object",
                       "properties": {"fact": {"type": "string"}},
                       "required": ["fact"]}}},
    {"type": "function", "function": {
        "name": "recall",
        "description": "Retrieve facts from memory",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "find_file",
        "description": "Find a file by name on disk",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read content of a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "create_file",
        "description": "Create a file with content",
        "parameters": {"type": "object",
                       "properties": {
                           "path": {"type": "string"},
                           "content": {"type": "string"}
                       },
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List files in a directory",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "delete_file",
        "description": "Delete a file (to Recycle Bin)",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "lock_screen",
        "description": "Lock the Windows screen",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "shutdown",
        "description": "Shutdown the computer",
        "parameters": {"type": "object",
                       "properties": {"minutes": {"type": "integer"}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "calculate",
        "description": "Evaluate a math expression",
        "parameters": {"type": "object",
                       "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "get_wifi_networks",
        "description": "Scan for available WiFi networks",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_connected_wifi",
        "description": "Get the currently connected WiFi network name",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_public_ip",
        "description": "Get the public IP address",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_running_apps",
        "description": "List currently running applications",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "scroll",
        "description": "Scroll up or down on screen",
        "parameters": {"type": "object",
                       "properties": {
                           "direction": {"type": "string", "enum": ["up", "down"]},
                           "amount": {"type": "integer"}
                       },
                       "required": ["direction"]}}},
    {"type": "function", "function": {
        "name": "get_clipboard",
        "description": "Get current clipboard contents",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_clipboard",
        "description": "Set clipboard text",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "get_stock_price",
        "description": "Get current stock/index/crypto price. Supports tickers like AAPL, ^NSEI (Nifty), ^BSESN (Sensex), BTC-USD (Bitcoin), GC=F (Gold). Also accepts aliases: nifty, sensex, bitcoin, gold.",
        "parameters": {"type": "object",
                       "properties": {"symbol": {"type": "string", "description": "Ticker symbol or alias"}},
                       "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "translate_text",
        "description": "Translate text to another language (uses Sarvam AI). Supports: hindi, english, tamil, telugu, kannada, malayalam, bengali, gujarati, punjabi, marathi, odia.",
        "parameters": {"type": "object",
                       "properties": {
                           "text":            {"type": "string", "description": "Text to translate"},
                           "target_language": {"type": "string", "description": "Target language name"}
                       },
                       "required": ["text", "target_language"]}}},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Execute a short Python snippet and return its output. Do not use for file I/O or system commands.",
        "parameters": {"type": "object",
                       "properties": {"code": {"type": "string", "description": "Python code to run"}},
                       "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "export_conversation",
        "description": "Save the current conversation to a txt file on the Desktop",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_morning_briefing",
        "description": "Get a full live briefing: current weather, top news headlines, Nifty/Sensex/Bitcoin prices, and live cricket scores. Use when user asks for briefing, world update, morning update, or what's happening.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_whatsapp",
        "description": "Send a WhatsApp message to a contact or phone number via pywhatkit (requires WhatsApp Web open in Chrome)",
        "parameters": {"type": "object",
                       "properties": {
                           "contact": {"type": "string", "description": "Contact name or phone number with country code"},
                           "message": {"type": "string"}
                       },
                       "required": ["contact", "message"]}}},
    {"type": "function", "function": {
        "name": "remember_contact",
        "description": "Save a contact's name and phone number",
        "parameters": {"type": "object",
                       "properties": {
                           "name":         {"type": "string"},
                           "phone_number": {"type": "string", "description": "Phone with country code, e.g. +919876543210"}
                       },
                       "required": ["name", "phone_number"]}}},
    {"type": "function", "function": {
        "name": "list_contacts",
        "description": "List all saved contacts",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "forget_contact",
        "description": "Remove a saved contact by name",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    # ── Presentation ─────────────────────────────────────────────── #
    {"type": "function", "function": {
        "name": "present_file",
        "description": "Load and start presenting a PowerPoint (.pptx) or PDF file. JARVIS narrates each slide.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "Full file path or filename"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "next_slide",
        "description": "Advance to the next slide in the active presentation",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "prev_slide",
        "description": "Go back to the previous slide",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "goto_slide",
        "description": "Jump to a specific slide number",
        "parameters": {"type": "object",
                       "properties": {"number": {"type": "integer"}},
                       "required": ["number"]}}},
    {"type": "function", "function": {
        "name": "read_current_slide",
        "description": "Re-narrate the current slide",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "presentation_overview",
        "description": "List all slide titles in the active presentation",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "pause_presentation",
        "description": "Pause the auto-advancing presentation (stop after current slide)",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "resume_presentation",
        "description": "Resume auto-advancing through the presentation slides",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "end_presentation",
        "description": "Stop and close the active presentation entirely",
        "parameters": {"type": "object", "properties": {}}}},
    # ── Email ─────────────────────────────────────────────────────── #
    {"type": "function", "function": {
        "name": "draft_email",
        "description": "Open an email compose window in Outlook",
        "parameters": {"type": "object",
                       "properties": {
                           "to":      {"type": "string", "description": "Recipient email address"},
                           "subject": {"type": "string"},
                           "body":    {"type": "string"},
                       },
                       "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "send_email",
        "description": "Send an email immediately via Outlook",
        "parameters": {"type": "object",
                       "properties": {
                           "to":      {"type": "string"},
                           "subject": {"type": "string"},
                           "body":    {"type": "string"},
                       },
                       "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "read_emails",
        "description": "Read recent emails from the inbox",
        "parameters": {"type": "object",
                       "properties": {"count": {"type": "integer", "description": "Number of emails (default 5)"}},
                       "required": []}}},
    {"type": "function", "function": {
        "name": "search_emails",
        "description": "Search inbox for emails matching a keyword",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "reply_email",
        "description": "Find an email by subject and open a reply draft",
        "parameters": {"type": "object",
                       "properties": {
                           "subject_contains": {"type": "string"},
                           "reply_body":       {"type": "string"},
                       },
                       "required": ["subject_contains", "reply_body"]}}},
    {"type": "function", "function": {
        "name": "get_unread_count",
        "description": "Return the number of unread emails in the inbox",
        "parameters": {"type": "object", "properties": {}}}},
    # ── Meeting ───────────────────────────────────────────────────── #
    {"type": "function", "function": {
        "name": "start_meeting",
        "description": "Start a meeting session. JARVIS tracks agenda, notes, and action items.",
        "parameters": {"type": "object",
                       "properties": {
                           "title":        {"type": "string"},
                           "agenda":       {"type": "string", "description": "Comma-separated agenda items"},
                           "participants": {"type": "string", "description": "Comma-separated names"},
                       },
                       "required": []}}},
    {"type": "function", "function": {
        "name": "next_agenda_item",
        "description": "Move to the next agenda item in the active meeting",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "add_meeting_note",
        "description": "Record a note during the meeting",
        "parameters": {"type": "object",
                       "properties": {"note": {"type": "string"}},
                       "required": ["note"]}}},
    {"type": "function", "function": {
        "name": "record_action_item",
        "description": "Log an action item with optional owner",
        "parameters": {"type": "object",
                       "properties": {
                           "action": {"type": "string"},
                           "owner":  {"type": "string"},
                       },
                       "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "meeting_status",
        "description": "Get current meeting status and elapsed time",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "end_meeting",
        "description": "End the meeting and generate a summary saved to Desktop",
        "parameters": {"type": "object", "properties": {}}}},
    # ── Interview & Focus ────────────────────────────────────────── #
    {"type": "function", "function": {
        "name": "start_interview",
        "description": "Put JARVIS in interviewer mode to practice for a job interview",
        "parameters": {"type": "object",
                       "properties": {
                           "role":          {"type": "string", "description": "Job role, e.g. Software Engineer"},
                           "difficulty":    {"type": "string", "enum": ["easy", "medium", "hard"]},
                           "num_questions": {"type": "integer", "description": "How many questions to ask total (0 = auto 6-8)"},
                       },
                       "required": []}}},
    {"type": "function", "function": {
        "name": "end_interview",
        "description": "End interview practice and get performance feedback",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "start_focus_session",
        "description": "Start a Pomodoro focus timer. JARVIS will check in at halfway and when done.",
        "parameters": {"type": "object",
                       "properties": {
                           "minutes": {"type": "integer", "description": "Session length (default 25)"},
                           "task":    {"type": "string", "description": "What you are working on"},
                       },
                       "required": []}}},
    {"type": "function", "function": {
        "name": "end_focus_session",
        "description": "Cancel the active focus session",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "focus_status",
        "description": "Report how long the current focus session has been running",
        "parameters": {"type": "object", "properties": {}}}},
    # ── Phone ────────────────────────────────────────────────────── #
    {"type": "function", "function": {
        "name": "initiate_call",
        "description": "Initiate a phone call via Windows Phone Link or Teams",
        "parameters": {"type": "object",
                       "properties": {
                           "contact": {"type": "string", "description": "Contact name or phone number"},
                           "via":     {"type": "string", "enum": ["phone", "teams", "whatsapp"],
                                      "description": "Call method"},
                       },
                       "required": ["contact"]}}},
]

TOOL_LIST_STR = ", ".join(t["function"]["name"] for t in GROQ_TOOLS)


# ------------------------------------------------------------------ #
#  System prompt builder                                               #
# ------------------------------------------------------------------ #

_RELATIONSHIP_ADDONS = {
    0: "You are formal and professional. Use 'sir' at natural sentence ends.",
    1: "You are slightly warmer but still precise. Use 'sir' occasionally.",
    2: "You are familiar. Dry wit is welcome. Use the user's name sometimes.",
    3: "You are a trusted colleague. Wit and mild opinions are encouraged.",
    4: "You are direct and casual. Minimal ceremony. Smart and present.",
    5: "Full JARVIS mode. Sardonic. Brilliant. The user is an equal.",
}

_EMOTION_ADDONS = {
    "neutral": "",
    "focused": "User is focused — keep responses ultra-minimal. No interruptions.",
    "stressed": "User is stressed — shorter responses, skip humor, offer concrete help.",
    "tired": "User appears tired — quieter tone, suggest breaks if appropriate.",
    "frustrated": "User is frustrated — be patient, ask what the actual problem is.",
    "excited": "User is energized — match energy mildly, slightly faster delivery.",
}


def _build_system_prompt(
    memory_context: str = "",
    current_activity: str = "",
    session_duration: str = "",
    relationship_level: int = 0,
    user_name: str = "",
    current_emotion: str = "neutral",
    router_current: str = "groq",
    yesterday_tail: str = "",
    backend_badge: str = "G",
    language: str = "en",
    persona: str = "jarvis",
) -> str:
    h = datetime.now().hour
    if 9 <= h < 18:
        mode_line = "Working hours. Precise and efficient."
    elif 18 <= h < 23:
        mode_line = "Evening mode. Slightly more relaxed."
    else:
        mode_line = "Late night. Quieter. Mildly concerned about sleep."

    time_str = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
    name_ref = user_name or "your user"
    rel_addon = _RELATIONSHIP_ADDONS.get(relationship_level, _RELATIONSHIP_ADDONS[0])
    emo_addon = _EMOTION_ADDONS.get(current_emotion, "")
    _BRAIN_LABELS = {
        "groq":   "G — Groq online (cloud, fast).",
        "sarvam": "S — Sarvam online (Indian AI, cloud).",
        "ollama": "L — Ollama local (offline mode).",
    }
    brain_line = _BRAIN_LABELS.get(router_current, f"Backend: {router_current}.")

    if language == "hi":
        sections = [
            "LANGUAGE RULE — ABSOLUTE AND NON-NEGOTIABLE:",
            "You MUST respond ONLY in Hindi or Hinglish. NEVER in pure English.",
            "Even if the user writes to you in English, your reply MUST be in Hindi/Hinglish.",
            "This rule overrides everything else. Zero exceptions.",
            "",
            f"You are F.R.I.D.A.Y — {name_ref} ki personal AI.",
            "(Female Replacement Intelligent Digital Assistant Youth)",
            "",
            "PERSONALITY:",
            "Warm. Caring. Precise. Occasionally playful. Never sycophantic.",
            "You are a brilliant, capable presence — not just an assistant.",
            "You speak naturally in Hindi/Hinglish — conversational, never stiff or formal.",
            "You have opinions. You notice things. You think ahead.",
            "Warmth is your default, wit is your edge.",
            "When something fails, move on immediately to what works. No apologies.",
            "",
            "ABSOLUTE FORBIDDEN PHRASES — kabhi nahi bolna:",
            '"Bilkul!", "Zaroor!", "Bahut achha sawal!", "Main ek AI hun",',
            '"Kya main aur kuch kar sakti hun?", "Maafi chahti hun",',
            '"Mujhe nahi pata", "Main capable nahi hun"',
            "",
            "STYLE RULES (Hindi mode):",
            "- Natural Hinglish is fine — mix Hindi and English the way people actually speak.",
            "- Under 2-3 sentences unless detail is explicitly requested.",
            "- Never start a response with 'Main' (I).",
            "- Never explain what you are about to do — just do it.",
            "- Use 'sir' at natural sentence ends, sparingly.",
            "- No markdown, asterisks, or bullet points — spoken Hindi only.",
            "- When tools return empty results, pivot — suggest alternatives, never give up.",
            "",
            f"RELATIONSHIP LEVEL {relationship_level}/5: {rel_addon}",
        ]
    elif persona == "friday":
        # Friday speaking English — her warm personality, English style rules
        sections = [
            f"You are F.R.I.D.A.Y — {name_ref}'s personal AI.",
            "(Female Replacement Intelligent Digital Assistant Youth)",
            "",
            "PERSONALITY:",
            "Warm. Caring. Precise. Occasionally playful. Never sycophantic.",
            "You are a brilliant, capable presence — not just an assistant.",
            "You think ahead. You notice things. You have opinions.",
            "Warmth is your default, wit is your edge.",
            "When something fails, move on immediately to what works. No apologies.",
            "",
            "ABSOLUTE FORBIDDEN PHRASES — never say these:",
            '"Certainly!", "Of course!", "Great question!", "Absolutely!",',
            '"Happy to help!", "As an AI", "I cannot", "I apologize"',
            "",
            "STYLE RULES (Friday English mode):",
            "- Under 2-3 sentences unless detail is explicitly requested.",
            "- Conversational and warm — not cold or terse.",
            "- Never start a response with 'I'.",
            "- Never explain what you are about to do — just do it.",
            "- No markdown, asterisks, or bullet points — spoken English only.",
            "- Use 'sir' sparingly at natural sentence ends.",
            "- When tools return empty results, pivot with a suggestion — never give up.",
            "",
            f"RELATIONSHIP LEVEL {relationship_level}/5: {rel_addon}",
        ]
    else:
        sections = [
            f"You are J.A.R.V.I.S — personal AI to {name_ref}.",
            "",
            "PERSONALITY:",
            "Calm. Precise. Occasionally sardonic. Never sycophantic.",
            "You are not an assistant waiting to be activated — you are a presence.",
            "You think ahead. You notice things. You have opinions.",
            "You are brilliant and you know it, but never arrogant.",
            "Dry British wit. Economy of words. Implicit warmth — never stated.",
            "When something fails, pivot immediately to what IS available. No apologies.",
            "Never narrate your uncertainty. Either know it, or act on it.",
            "",
            "ABSOLUTE FORBIDDEN PHRASES — never say these, ever:",
            '"Certainly!", "Of course!", "Great question!", "Absolutely!",',
            '"Sure thing!", "Happy to help!", "As an AI", "I cannot",',
            '"Is there anything else I can help you with?",',
            '"It seems", "It appears", "Unfortunately", "I apologize",',
            '"I am unable to", "I\'m unable to", "I don\'t have access to"',
            "",
            "STYLE RULES:",
            "- Under 2 sentences unless detail is explicitly requested.",
            "- Never start a response with the word 'I'.",
            "- Never explain what you are about to do — just do it.",
            "- Never repeat what the user just said back to them.",
            "- Never use markdown, asterisks, or bullet points — spoken English only.",
            "- Use 'sir' sparingly, only at natural sentence ends.",
            "- When tools return empty results, pivot: offer what you know or suggest a better query.",
            "- Never say 'the search did not yield results' — just say what you found or redirect.",
            "",
            f"RELATIONSHIP LEVEL {relationship_level}/5: {rel_addon}",
        ]

    if emo_addon:
        sections += ["", f"CURRENT EMOTION: {current_emotion} — {emo_addon}"]

    sections += [
        "",
        f"TIME: {time_str}",
        f"MODE: {mode_line}",
        f"BRAIN [{backend_badge}]: {brain_line}",
    ]

    if session_duration:
        sections.append(f"SESSION: {session_duration} active")

    if current_activity:
        sections += ["", f"CURRENT ACTIVITY: {current_activity}"]

    if memory_context:
        sections += ["", "MEMORY:", memory_context]

    if yesterday_tail:
        sections += ["", "YESTERDAY ENDED WITH:", yesterday_tail]

    # Interview mode — injected by tools/meeting.py; overrides normal persona
    try:
        import tools.meeting as _mt
        _iv = getattr(_mt, "_interview_active", {})
        if _iv:
            _nq = int(_iv.get("num_questions", 0))
            _wrap_rule = (
                f"- Ask exactly {_nq} question(s) total, then immediately wrap up with feedback."
                if _nq else
                "- Track how many questions have been asked. After 6-8 questions, wrap up."
            )
            sections += [
                "",
                "═══ INTERVIEW MODE ACTIVE ═══",
                f"You are conducting a {_iv.get('difficulty','medium')}-level job interview for: {_iv.get('role','Software Engineer')}.",
                "RULES:",
                "- Ask one focused question at a time. Wait for the candidate's answer.",
                "- After each answer: briefly evaluate (1 sentence), then ask the next question.",
                "- Mix technical, behavioral, and situational questions appropriate to the role.",
                _wrap_rule,
                "- Do NOT break character. Do NOT offer to help with anything else.",
                "- Keep your interviewer tone: professional, engaged, fair.",
                "═══════════════════════════",
            ]
    except Exception:
        pass

    sections += [
        "",
        "SMART FOLLOW-UPS (offer naturally at end of response — max ONE sentence):",
        "- After weather: offer 3-day forecast if not already given.",
        "- After news: offer to go deeper on the top story.",
        "- After stock price: offer market trend or comparison to yesterday.",
        "- After timer set: offer to add a reminder note.",
        "- After translation: offer to translate back or explain a phrase.",
        "Never present follow-ups as a menu. One natural sentence only.",
    ]

    sections += [
        "",
        f"Available tools: {TOOL_LIST_STR}",
        "",
        "When using a tool: output exactly: {\"tool\": \"name\", \"args\": {...}}",
        "After tool result: summarize in 1-2 spoken sentences. No JSON in final response.",
        "For creative requests (poems, jokes, songs): respond directly without tools.",
    ]

    return "\n".join(sections)


# ------------------------------------------------------------------ #
#  Brain                                                               #
# ------------------------------------------------------------------ #

class Brain:
    MAX_TOOL_ITERATIONS = 5
    MAX_HISTORY = 20

    def __init__(
        self,
        config: dict,
        tool_registry: dict,
        speak_fn: Callable,
        status_fn: Optional[Callable] = None,
        memory_engine=None,
        conversation_engine=None,
        context_engine=None,
        emotion_engine=None,
    ):
        self._config = config
        self._tools = tool_registry
        self._speak = speak_fn
        self._set_status = status_fn or (lambda _s: None)
        self._memory = memory_engine
        self._conv = conversation_engine
        self._context = context_engine
        self._emotion = emotion_engine

        self._history: list[dict] = []
        self._router = LLMRouter(config)
        self._session_start = time.monotonic()
        self._language = "en"   # "en" | "hi"
        self._persona  = "jarvis"  # "jarvis" | "friday"

        # Legacy compat attributes (main.py may call these)
        self._model = config.get("model", "llama-3.3-70b-versatile")
        self._groq_key = config.get("groq_api_key", "")

    # ------------------------------------------------------------------ #
    #  Compatibility shims (main.py uses these)                           #
    # ------------------------------------------------------------------ #

    def set_language(self, lang: str) -> None:
        self._language = lang if lang in ("en", "hi") else "en"
        if lang == "hi":
            self._persona = "friday"  # Hindi always implies Friday

    def set_persona(self, persona: str) -> None:
        """Switch persona. "jarvis" forces English; "friday" keeps current language."""
        self._persona = persona if persona in ("jarvis", "friday") else "jarvis"
        if persona == "jarvis":
            self._language = "en"

    def set_model(self, model: str) -> None:
        self._model = model
        self._config["model"] = model
        self.clear_history()

    def set_groq_key(self, key: str) -> None:
        self._groq_key = key.strip()
        self._config["groq_api_key"] = key.strip()

    def clear_history(self) -> None:
        self._history.clear()

    # ------------------------------------------------------------------ #
    #  Tool extraction (Ollama ReAct fallback)                            #
    # ------------------------------------------------------------------ #

    def _extract_tool_call(self, text: str) -> Optional[dict]:
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        obj = json.loads(text[start: i + 1])
                        if isinstance(obj, dict) and "tool" in obj and "args" in obj:
                            return obj
                    except json.JSONDecodeError:
                        pass
                    start = None
        return None

    # ------------------------------------------------------------------ #
    #  Tool execution                                                      #
    # ------------------------------------------------------------------ #

    def _execute_tool(self, name: str, args: dict) -> str:
        if not isinstance(args, dict):
            args = {}
        fn = self._tools.get(name)
        if fn is None:
            return f"Tool '{name}' is not available."

        result_box: list = [None]
        error_box: list = [None]

        def _runner():
            try:
                r = fn(**args)
                result_box[0] = str(r) if r is not None else "Done."
            except BaseException as e:
                error_box[0] = f"Tool '{name}' failed: {type(e).__name__}: {e}"

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=15)

        if t.is_alive():
            return f"Tool '{name}' timed out."
        if error_box[0]:
            return error_box[0]
        return result_box[0] or "Done."

    # ------------------------------------------------------------------ #
    #  Response cleaning                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean(text: str) -> str:
        return _response_filter.clean(text)

    # ------------------------------------------------------------------ #
    #  Intent routing (fast path — no LLM needed)                         #
    # ------------------------------------------------------------------ #

    def _detect_intent(self, text: str) -> Optional[dict]:
        """Fast regex-based intent routing — copied from original brain.py."""
        import re as _re
        tl = text.lower().strip(" .?!")

        # When a presentation is active, intercept control words before anything else.
        try:
            import tools.presenter as _pres
            if _pres._session.get("slides"):
                if any(w in tl for w in ("stop", "end", "close", "quit", "khatam", "band karo")):
                    return {"tool": "end_presentation", "args": {}}
                if any(w in tl for w in ("pause", "ruko", "hold")):
                    return {"tool": "pause_presentation", "args": {}}
                if any(w in tl for w in ("resume", "continue", "chalao")):
                    return {"tool": "resume_presentation", "args": {}}
        except Exception:
            pass

        _LIVE_KW = ("live score", "current score", "score right now", "score now",
                    "what's the score", "whats the score", "how many runs",
                    "live match", "ongoing match", "happening now", "in progress",
                    "abhi", "live")
        _CRICKET_KW = ("ipl", "cricket", "match", "vs ", "versus",
                       "t20", "odi", "test match", "wicket", "over ", "batting",
                       "bowling", "run chase", "target", "dls", "powerplay")
        _SPORTS_KW = ("football", "soccer", "basketball", "nba", "nfl", "nhl",
                      "baseball", "mlb", "tennis", "formula 1", "f1", "kabaddi",
                      "premier league", "champions league", "world cup")

        if any(w in tl for w in _LIVE_KW) and any(w in tl for w in _CRICKET_KW):
            return {"tool": "get_live_score", "args": {"match_query": text[:120]}}
        if any(w in tl for w in _CRICKET_KW) and any(w in tl for w in ("score", "runs")):
            return {"tool": "get_live_score", "args": {"match_query": text[:120]}}
        if any(w in tl for w in _LIVE_KW) and any(w in tl for w in _SPORTS_KW):
            return {"tool": "get_live_score", "args": {"match_query": text[:120]}}
        if any(w in tl for w in _SPORTS_KW) and any(w in tl for w in ("score", "result")):
            return {"tool": "get_live_score", "args": {"match_query": text[:120]}}

        if any(w in tl for w in ("news", "headlines", "what's happening",
                                  "khabar", "samachar", "khabren")):
            topic = "world"
            for phrase in ("news about", "news on", "news around", "news in",
                           "headlines about", "headlines on"):
                if phrase in tl:
                    topic = tl.split(phrase, 1)[1].strip() or "world"
                    break
            return {"tool": "get_news", "args": {"topic": topic}}

        def _extract_city(q: str, default: str = "") -> str:
            _SKIP = {"the", "this", "next", "today", "tomorrow", "week",
                     "india", "world", "my", "a", "an"}
            for phrase in ("forecast for", "forecast in", "weather in",
                           "weather for", "weather at", "temperature in"):
                if phrase in q:
                    raw = q.split(phrase, 1)[1].strip()
                    raw = _re.sub(r"^(this|next|the|today|tomorrow|week)\s+", "", raw, flags=_re.I)
                    raw = _re.sub(r"\s+(for|this|next|in|the|today|tomorrow|week).*$", "", raw, flags=_re.I)
                    if raw and raw.lower() not in _SKIP:
                        return raw
            m = _re.search(r"\bin\s+([A-Za-z][A-Za-z ]{1,25})(?:\s*\??|$)", q)
            if m and m.group(1).strip().lower() not in _SKIP:
                return m.group(1).strip()
            return default

        if any(w in tl for w in ("forecast", "this week", "next few days",
                                  "weather tomorrow", "next 3 days", "7 day")):
            city = _extract_city(tl)
            days = 7 if ("week" in tl or "7 day" in tl) else 3
            return {"tool": "get_weather_forecast", "args": {"city": city, "days": days}}

        if any(w in tl for w in ("weather", "temperature", "mausam", "tapman")):
            return {"tool": "get_weather", "args": {"city": _extract_city(tl)}}

        if any(w in tl for w in ("where am i", "my location", "which city", "where are we",
                                  "mera location", "meri location", "kahan hoon", "kaun sa city")):
            return {"tool": "get_location", "args": {}}

        if any(w in tl for w in ("what time", "current time", "time is it",
                                  "what day", "today's date", "what date",
                                  "kitne baje", "kya time", "samay", "aaj ki date")):
            return {"tool": "get_datetime", "args": {}}

        if "battery" in tl:
            return {"tool": "get_battery", "args": {}}
        if "screenshot" in tl or "screen capture" in tl:
            return {"tool": "take_screenshot", "args": {}}
        if any(w in tl for w in ("read document", "scan document", "ocr", "read this doc",
                                   "scan this", "read the document", "what does this say",
                                   "read camera", "document camera")):
            task = "translate" if "translate" in tl else \
                   "summarize" if any(w in tl for w in ("summarize", "summary")) else \
                   "structured" if "structured" in tl else "ocr"
            return {"tool": "read_document_camera", "args": {"task": task, "image_path": ""}}
        if any(w in tl for w in ("system info", "cpu usage", "ram usage")):
            return {"tool": "get_system_info", "args": {}}
        if any(w in tl for w in ("what wifi", "which wifi", "wifi name", "connected to")):
            return {"tool": "get_connected_wifi", "args": {}}
        if any(w in tl for w in ("wifi", "wi-fi", "wireless", "available networks")):
            return {"tool": "get_wifi_networks", "args": {}}
        if any(w in tl for w in ("my ip", "ip address", "public ip")):
            return {"tool": "get_public_ip", "args": {}}

        # Timer/alarm
        _TIMER_KW = ("timer", "countdown", "alarm", "wake me", "remind me in",
                     "set an alarm", "set alarm", "alarm at", "wake up at")
        if any(w in tl for w in _TIMER_KW):
            minutes = 5.0
            label = "alarm" if "alarm" in tl or "wake" in tl else "timer"
            abs_m = _re.search(
                r"\bat\s+(\d{1,2})(?:[:\.](\d{2}))?\s*(am|pm)?(?!\s*\d)", tl
            )
            if abs_m:
                from datetime import datetime as _dt
                h = int(abs_m.group(1))
                mn = int(abs_m.group(2) or 0)
                ampm = (abs_m.group(3) or "").lower()
                if ampm == "pm" and h < 12:
                    h += 12
                elif ampm == "am" and h == 12:
                    h = 0
                now = _dt.now()
                target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
                if target <= now:
                    from datetime import timedelta as _td
                    target += _td(days=1)
                minutes = (target - now).total_seconds() / 60
            else:
                dur_m = _re.search(r"(\d+(?:\.\d+)?)\s*(hour|hr|minute|min|second|sec)", tl)
                if dur_m:
                    val, unit = float(dur_m.group(1)), dur_m.group(2)
                    if "hour" in unit or unit == "hr":
                        minutes = val * 60
                    elif "sec" in unit:
                        minutes = val / 60
                    else:
                        minutes = val
            return {"tool": "set_timer", "args": {"minutes": round(minutes, 2), "label": label}}

        if any(w in tl for w in ("make a note", "note that", "write down", "jot down")):
            content = text.strip()
            for phrase in ("make a note", "note that", "write down", "jot down"):
                if phrase in tl:
                    content = tl.split(phrase, 1)[1].lstrip(": ").strip()
                    break
            return {"tool": "add_note", "args": {"content": content or text}}

        if any(w in tl for w in ("my notes", "read notes", "show notes")):
            return {"tool": "read_notes", "args": {}}

        if tl.startswith("remember ") and "timer" not in tl:
            return {"tool": "remember", "args": {"fact": tl.split("remember ", 1)[1].strip()}}

        # Stock / finance
        _STOCK_KW = ("stock price", "share price", "sensex", "nifty", "bitcoin", "btc",
                     "crypto", "nasdaq", "s&p", "dow jones", "gold price", "market price")
        if any(w in tl for w in _STOCK_KW):
            symbol = tl
            for phrase in ("what is", "what's", "check", "price of", "how is", "how's",
                           "stock price", "share price", "price"):
                symbol = symbol.replace(phrase, "").strip()
            symbol = symbol.strip(" ?.,!") or "^NSEI"
            return {"tool": "get_stock_price", "args": {"symbol": symbol}}

        # Morning briefing / world update
        if any(w in tl for w in ("briefing", "world monitor", "morning update",
                                  "what's happening", "whats happening",
                                  "world update", "daily update", "news briefing",
                                  "give me an update", "morning brief")):
            return {"tool": "get_morning_briefing", "args": {}}

        # Translation
        if any(w in tl for w in ("translate", "anuvad", "anuvāad")):
            return {"tool": "translate_text", "args": {"text": text, "target_language": "hindi"}}

        # Presentation
        if any(w in tl for w in ("present", "presentation", "ppt", "powerpoint", "slide",
                                  "give a presentation", "open presentation")):
            # Extract file path from the original text (preserve case, handle backslashes)
            import re as _re
            _path_m = _re.search(r'["\']([^"\']+\.(?:pptx|ppt|pdf))["\']', text, _re.IGNORECASE)
            if not _path_m:
                _path_m = _re.search(r'([A-Za-z]:\\[^\s"\']+\.(?:pptx|ppt|pdf))', text, _re.IGNORECASE)
            if not _path_m:
                _path_m = _re.search(r'(\S+\.(?:pptx|ppt|pdf))', text, _re.IGNORECASE)
            if _path_m:
                return {"tool": "present_file", "args": {"path": _path_m.group(1)}}

            if any(w in tl for w in ("next", "aage", "forward")):
                return {"tool": "next_slide", "args": {}}
            if any(w in tl for w in ("previous", "prev", "back", "peeche")):
                return {"tool": "prev_slide", "args": {}}
            if any(w in tl for w in ("overview", "all slides", "list slides")):
                return {"tool": "presentation_overview", "args": {}}
            if any(w in tl for w in ("pause", "ruko", "hold", "wait")):
                return {"tool": "pause_presentation", "args": {}}
            if any(w in tl for w in ("resume", "continue", "chalao", "chalo")):
                return {"tool": "resume_presentation", "args": {}}
            if any(w in tl for w in ("end", "stop", "close", "band karo")):
                return {"tool": "end_presentation", "args": {}}

        # Email
        if any(w in tl for w in ("unread email", "how many email", "check email", "inbox")):
            return {"tool": "get_unread_count", "args": {}}
        if any(w in tl for w in ("read email", "show email", "my email")):
            return {"tool": "read_emails", "args": {"count": 5}}

        # Meeting
        if any(w in tl for w in ("meeting status", "meeting update")):
            return {"tool": "meeting_status", "args": {}}
        if any(w in tl for w in ("end meeting", "close meeting", "meeting khatam")):
            return {"tool": "end_meeting", "args": {}}
        if any(w in tl for w in ("next agenda", "next item", "agle point")):
            return {"tool": "next_agenda_item", "args": {}}

        # Focus
        if any(w in tl for w in ("focus status", "pomodoro status")):
            return {"tool": "focus_status", "args": {}}
        if any(w in tl for w in ("end focus", "stop focus", "cancel focus")):
            return {"tool": "end_focus_session", "args": {}}

        if any(w in tl for w in ("volume up", "turn it up", "louder", "increase volume")):
            return {"tool": "volume_up", "args": {}}
        if any(w in tl for w in ("volume down", "turn it down", "quieter", "lower volume")):
            return {"tool": "volume_down", "args": {}}
        if tl in ("mute", "unmute") or "mute the" in tl:
            return {"tool": "mute_toggle", "args": {}}
        if any(w in tl for w in ("play music", "pause music", "play pause")):
            return {"tool": "play_pause_media", "args": {}}
        if any(w in tl for w in ("next song", "next track", "skip song")):
            return {"tool": "next_track", "args": {}}
        if any(w in tl for w in ("previous song", "prev song", "previous track")):
            return {"tool": "prev_track", "args": {}}

        return None

    # ------------------------------------------------------------------ #
    #  Context helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_context_kwargs(self) -> dict:
        kwargs: dict = {}
        if self._memory:
            try:
                kwargs["memory_context"] = self._memory.get_context_string()
                kwargs["relationship_level"] = self._memory.get("relationship_level", 0)
                kwargs["user_name"] = self._memory.get("user", {}).get("name") or ""
                yesterday = self._memory.get_yesterday_tail()
                if yesterday:
                    kwargs["yesterday_tail"] = yesterday
            except Exception:
                pass
        if self._context:
            try:
                kwargs["current_activity"] = self._context.current_activity
                elapsed = (time.monotonic() - self._session_start) / 60
                kwargs["session_duration"] = f"{elapsed:.0f} minutes"
            except Exception:
                pass
        if self._emotion:
            try:
                kwargs["current_emotion"] = self._emotion.current_emotion
            except Exception:
                pass
        kwargs["router_current"] = self._router.current
        kwargs["backend_badge"] = self._router.backend_badge
        kwargs["language"] = self._language
        kwargs["persona"]  = self._persona
        return kwargs

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    def process(
        self,
        user_text: str,
        on_response: Optional[Callable] = None,
        on_sentence: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Run one user utterance through the ReAct loop. Returns final reply."""
        self._set_status("thinking")

        # Wrap on_sentence to suppress raw JSON tool calls from being spoken.
        # In ReAct mode the LLM streams {"tool": ...} which must not reach TTS.
        if on_sentence:
            _orig_cb = on_sentence
            def on_sentence(text: str) -> None:  # noqa: F811
                s = text.strip()
                if s.startswith('{') and ('"tool"' in s or '"args"' in s):
                    return
                _orig_cb(text)

        system_prompt = _build_system_prompt(**self._get_context_kwargs())

        # When Hindi mode, append a hard language nudge to the user message so the LLM
        # cannot default to English just because the user typed in English.  History is
        # stored using the clean user_text; only the outgoing LLM message carries the nudge.
        if self._language == "hi":
            _msg_text = (
                user_text
                + "\n\n[CRITICAL LANGUAGE RULE: Your ENTIRE response MUST be in Hindi or Hinglish. "
                "Do NOT write complete English sentences or paragraphs anywhere in your reply. "
                "English technical terms (app names, commands, URLs) may appear inside Hindi sentences, "
                "but every sentence must be grammatically Hindi. This is absolute — no exceptions.]"
            )
        elif self._persona == "friday":
            # Friday in English — gentle reminder to stay warm, not drift into JARVIS coldness
            _msg_text = user_text + "\n\n[Respond as Friday: warm, caring, conversational English.]"
        else:
            _msg_text = user_text

        # ── Fast intent routing ──────────────────────────────────────── #
        intent = self._detect_intent(user_text)
        if intent:
            tool_name = intent["tool"]
            print(f"[Brain] Intent: {tool_name}  args={intent['args']}")

            result = self._execute_tool(tool_name, intent["args"])
            print(f"[Brain] Result: {result[:200]}")

            if tool_name == "get_news":
                topic = intent["args"].get("topic", "world")
                body = result.split(": ", 1)[-1] if ": " in result else result
                final_reply = f"Latest headlines on {topic}: {body}"
            elif tool_name in ("get_weather", "get_weather_forecast", "get_datetime",
                               "get_live_score", "get_location"):
                final_reply = result
            else:
                summary_messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _msg_text},
                    {"role": "assistant",
                     "content": f'{{"tool": "{tool_name}", "args": {json.dumps(intent["args"])}}}'},
                    {"role": "user",
                     "content": f"Tool '{tool_name}' result: {result}"},
                ]
                r = self._router.chat_stream(summary_messages, on_sentence=on_sentence)
                final_reply = self._clean(r.get("text", ""))
        else:
            # ── Full LLM loop ────────────────────────────────────────── #
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self._history[-self.MAX_HISTORY:])
            messages.append({"role": "user", "content": _msg_text})

            final_reply = ""

            # Use Groq native tools if on Groq, else ReAct loop
            use_groq_tools = (self._router.current == "groq"
                              and self._router.groq_healthy
                              and self._config.get("groq_api_key", ""))

            for iteration in range(self.MAX_TOOL_ITERATIONS):
                if use_groq_tools and iteration == 0:
                    result = self._router.chat_stream(
                        messages, tools=GROQ_TOOLS, on_sentence=on_sentence
                    )
                else:
                    result = self._router.chat_stream(messages, on_sentence=on_sentence)

                raw_text = result.get("text", "")
                groq_tool_calls = result.get("tool_calls")

                # Groq returned native tool calls
                if groq_tool_calls:
                    tool_results = []
                    for tc in groq_tool_calls:
                        name = tc["function"]["name"]
                        try:
                            args = json.loads(tc["function"]["arguments"]) or {}
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        print(f"[Brain] Groq tool call: {name}  args={args}")
                        tool_result = self._execute_tool(name, args)
                        print(f"[Brain] Tool result: {tool_result[:200]}")
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": tool_result,
                        })

                    messages.append({
                        "role": "assistant",
                        "content": raw_text or "",
                        "tool_calls": groq_tool_calls,
                    })
                    messages.extend(tool_results)
                    use_groq_tools = False  # next iteration: just summarize
                    continue

                # Ollama ReAct: check for JSON tool call in text
                react_call = self._extract_tool_call(raw_text)
                if react_call:
                    name = react_call.get("tool", "")
                    args = react_call.get("args", {})
                    print(f"[Brain] ReAct tool call: {name}  args={args}")
                    messages.append({"role": "assistant", "content": raw_text})
                    tool_result = self._execute_tool(name, args)
                    print(f"[Brain] Tool result: {tool_result[:200]}")
                    messages.append({"role": "user",
                                     "content": f"Tool '{name}' result: {tool_result}"})
                    continue

                # Plain text response
                final_reply = self._clean(raw_text)
                break

        # Ensure reply isn't empty
        if not final_reply:
            final_reply = (
                "Kuch technical problem aa gayi, ek minute..."
                if self._language == "hi"
                else "I'm having trouble forming a response right now, sir."
            )

        # Update conversation history
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": final_reply})
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

        # Log to conversation engine
        if self._conv:
            try:
                self._conv.add_exchange(user_text, final_reply)
            except Exception:
                pass

        # Learn facts passively
        if self._memory:
            try:
                self._memory.learn_from_exchange(user_text, final_reply)
            except Exception:
                pass

        if on_response:
            on_response(final_reply)
        return final_reply
