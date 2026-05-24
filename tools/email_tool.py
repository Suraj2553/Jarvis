"""tools/email_tool.py — Email via Outlook (win32com) or SMTP fallback.

Drafts, sends, reads, and replies to emails.
Outlook integration is zero-config if Outlook is installed.
SMTP fallback uses config gmail_user / gmail_app_password.
"""

import json
import os
import re
from datetime import datetime

_DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "JARVIS")
_DRAFTS_FILE = os.path.join(_DATA_DIR, "email_drafts.json")


def _cfg() -> dict:
    path = os.path.join(_DATA_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_outlook():
    try:
        import win32com.client
        return win32com.client.Dispatch("Outlook.Application")
    except Exception:
        return None


# ------------------------------------------------------------------ #
#  Send / Draft                                                        #
# ------------------------------------------------------------------ #

def draft_email(to: str, subject: str, body: str) -> str:
    """Open an email compose window in Outlook for review before sending."""
    outlook = _get_outlook()
    if outlook:
        try:
            mail = outlook.CreateItem(0)   # olMailItem
            mail.To = to
            mail.Subject = subject
            mail.Body = body
            mail.Display(False)
            return f"Email drafted to {to} with subject '{subject}'. It's open in Outlook — review and send when ready."
        except Exception as e:
            return f"Outlook error: {e}"

    # Fallback: save draft locally
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        drafts = []
        if os.path.exists(_DRAFTS_FILE):
            with open(_DRAFTS_FILE, encoding="utf-8") as f:
                drafts = json.load(f)
        drafts.append({"to": to, "subject": subject, "body": body,
                       "saved": datetime.now().isoformat()})
        with open(_DRAFTS_FILE, "w", encoding="utf-8") as f:
            json.dump(drafts, f, indent=2, ensure_ascii=False)
        return f"Draft saved locally (Outlook not found). To: {to} | Subject: {subject}"
    except Exception as e:
        return f"Could not save draft: {e}"


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email immediately via Outlook."""
    outlook = _get_outlook()
    if outlook:
        try:
            mail = outlook.CreateItem(0)
            mail.To = to
            mail.Subject = subject
            mail.Body = body
            mail.Send()
            return f"Email sent to {to}."
        except Exception as e:
            return f"Send failed via Outlook: {e}"

    # SMTP fallback (Gmail)
    cfg = _cfg()
    user = cfg.get("gmail_user", "")
    pwd  = cfg.get("gmail_app_password", "")
    if not user or not pwd:
        return "Outlook not available and Gmail credentials not configured. Add gmail_user and gmail_app_password to config."
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd)
            s.send_message(msg)
        return f"Email sent to {to} via Gmail."
    except Exception as e:
        return f"SMTP send failed: {e}"


# ------------------------------------------------------------------ #
#  Read                                                                #
# ------------------------------------------------------------------ #

def read_emails(count: int = 5) -> str:
    """Read recent inbox emails from Outlook."""
    outlook = _get_outlook()
    if not outlook:
        return "Outlook not available. Please install or open Outlook."
    try:
        ns = outlook.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)   # olFolderInbox
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        results = []
        for i, msg in enumerate(items):
            if i >= int(count):
                break
            try:
                sender  = msg.SenderName or msg.SenderEmailAddress or "Unknown"
                subj    = msg.Subject or "(no subject)"
                ts      = msg.ReceivedTime.strftime("%b %d %H:%M")
                unread  = "● " if not msg.UnRead else ""   # flag unread
                results.append(f"{unread}{i+1}. {sender} — {subj} [{ts}]")
            except Exception:
                break
        if not results:
            return "Inbox is empty."
        return "Recent emails:\n" + "\n".join(results)
    except Exception as e:
        return f"Could not read emails: {e}"


def search_emails(query: str) -> str:
    """Search inbox for emails matching a keyword."""
    outlook = _get_outlook()
    if not outlook:
        return "Outlook not available."
    try:
        ns    = outlook.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        q = query.lower()
        results = []
        for msg in items:
            try:
                if q in (msg.Subject or "").lower() or q in (msg.SenderName or "").lower():
                    ts = msg.ReceivedTime.strftime("%b %d")
                    results.append(f"• {msg.SenderName} — {msg.Subject} [{ts}]")
                    if len(results) >= 5:
                        break
            except Exception:
                break
        return ("Found:\n" + "\n".join(results)) if results else f"No emails matching '{query}'."
    except Exception as e:
        return f"Search failed: {e}"


def reply_email(subject_contains: str, reply_body: str) -> str:
    """Find email by subject keyword and open a reply draft."""
    outlook = _get_outlook()
    if not outlook:
        return "Outlook not available."
    try:
        ns    = outlook.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        q = subject_contains.lower()
        for msg in items:
            try:
                if q in (msg.Subject or "").lower():
                    reply = msg.Reply()
                    reply.Body = reply_body + "\n\n" + reply.Body
                    reply.Display(False)
                    return f"Reply drafted for '{msg.Subject}'. Open in Outlook — review and send."
            except Exception:
                continue
        return f"No email found with subject containing '{subject_contains}'."
    except Exception as e:
        return f"Reply failed: {e}"


def get_unread_count() -> str:
    """Return the number of unread emails in the inbox."""
    outlook = _get_outlook()
    if not outlook:
        return "Outlook not available."
    try:
        ns    = outlook.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)
        n = inbox.UnReadItemCount
        return f"You have {n} unread email{'s' if n != 1 else ''} in your inbox."
    except Exception as e:
        return f"Could not check email: {e}"
