"""
Subprocess-isolated DuckDuckGo caller.

Usage:
    python ddg_worker.py news   <topic>
    python ddg_worker.py search <query>

Prints a JSON list to stdout.  If lxml/curl_cffi inside ddgs causes a
C-level segfault, ONLY this subprocess dies — the JARVIS main process is
completely unaffected.
"""

import json
import sys

mode  = sys.argv[1] if len(sys.argv) > 1 else "news"
query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "world"

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print(json.dumps([]))
        sys.exit(0)

try:
    with DDGS() as ddgs:
        if mode == "search":
            results = list(ddgs.text(query, max_results=5))
        else:
            results = list(ddgs.news(query, max_results=7))
    print(json.dumps(results, ensure_ascii=False))
except Exception:
    print(json.dumps([]))
