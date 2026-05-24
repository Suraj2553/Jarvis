"""agent/autonomous_agent.py — Multi-step autonomous task execution.

Triggered by: "research", "find and", "make me a", "compare", "analyze",
"write a report", "create a".

Flow:
1. Plan via LLM → JSON step array
2. Confirm: JARVIS reads plan (max 2 sentences) "That'll take N steps. Shall I proceed?"
3. Execute step by step, feed outputs forward
4. HUD shows: "Step N of total: action"
5. Report: spoken summary of what was done

Max 8 steps. User can say "stop" at any point → clean abort.
"""

import json
import threading
from typing import Callable, Optional


_TRIGGER_WORDS = (
    "research", "find and", "make me a", "compare these",
    "analyze", "write a report", "create a", "compile a",
    "summarize and", "gather information",
)


def is_agentic_request(text: str) -> bool:
    """Return True if the user's request warrants multi-step autonomous execution."""
    tl = text.lower()
    return any(trigger in tl for trigger in _TRIGGER_WORDS)


class AutonomousAgent:
    """Executes multi-step tasks with user confirmation and HUD progress display."""

    MAX_STEPS = 8

    def __init__(
        self,
        router,
        tool_executor: Callable,
        speak_fn: Callable,
        status_fn: Optional[Callable] = None,
        hud_update_fn: Optional[Callable] = None,
    ):
        self._router = router
        self._execute_tool = tool_executor
        self._speak = speak_fn
        self._set_status = status_fn or (lambda _: None)
        self._hud_update = hud_update_fn or (lambda _: None)

        self._stop_requested = False
        self._running = False
        self._lock = threading.Lock()

    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True

    # ------------------------------------------------------------------ #
    #  Main execution                                                      #
    # ------------------------------------------------------------------ #

    def run(
        self,
        user_request: str,
        confirm_fn: Optional[Callable[[str], bool]] = None,
    ) -> str:
        """Execute an agentic task. Returns final summary.

        confirm_fn(plan_summary) → True to proceed, False to abort.
        If confirm_fn is None, auto-confirms.
        """
        with self._lock:
            self._stop_requested = False
            self._running = True

        try:
            # Step 1: Generate plan
            self._set_status("thinking")
            self._speak("Working out a plan.")
            plan = self._generate_plan(user_request)

            if not plan:
                return "I wasn't able to build a plan for that."

            # Trim to max steps
            plan = plan[:self.MAX_STEPS]
            n = len(plan)

            # Step 2: Confirm with user
            plan_summary = f"That'll take {n} steps: " + ", ".join(
                s.get("action", "?")[:30] for s in plan[:3]
            )
            if n > 3:
                plan_summary += f" and {n - 3} more"
            plan_summary += ". Shall I proceed?"

            self._speak(plan_summary)

            if confirm_fn and not confirm_fn(plan_summary):
                return "Understood. Aborting."

            # Step 3: Execute each step
            context = f"User requested: {user_request}\n"
            results = []

            for i, step in enumerate(plan):
                with self._lock:
                    if self._stop_requested:
                        self._speak("Stopping as requested.")
                        return "Task aborted."

                action = step.get("action", "")
                tool = step.get("tool", "")
                args = step.get("args", {})

                # Update HUD
                self._hud_update(f"Step {i+1} of {n}: {action[:40]}")
                print(f"[Agent] Step {i+1}/{n}: {action}")

                if tool:
                    result = self._execute_tool(tool, args)
                    context += f"Step {i+1} ({action}): {result[:200]}\n"
                    results.append(result)
                else:
                    # LLM-only step
                    messages = [
                        {"role": "system",
                         "content": "You are executing a multi-step research task. "
                                    "Complete the current step based on context."},
                        {"role": "user", "content": context + f"\nCurrent step: {action}"},
                    ]
                    step_result = self._router.chat_sync(messages, max_tokens=200)
                    context += f"Step {i+1} ({action}): {step_result[:200]}\n"
                    results.append(step_result)

            # Step 4: Final summary
            summary_messages = [
                {"role": "system",
                 "content": "Summarize what was accomplished in 2 sentences. "
                            "Be specific and direct."},
                {"role": "user",
                 "content": f"Task: {user_request}\nResults:\n{context}"},
            ]
            summary = self._router.chat_sync(summary_messages, max_tokens=100)
            self._hud_update("")
            self._speak(summary)
            return summary

        except Exception as e:
            print(f"[Agent] Error: {e}")
            return f"I hit a problem mid-task: {str(e)[:60]}"
        finally:
            with self._lock:
                self._running = False

    def _generate_plan(self, request: str) -> list[dict]:
        """Ask LLM to generate a step-by-step plan as JSON."""
        messages = [
            {"role": "system",
             "content": (
                 "Generate a concise execution plan for the user's request. "
                 f"Return a JSON array of up to {self.MAX_STEPS} steps. "
                 "Each step: {\"action\": \"brief description\", \"tool\": \"tool_name_or_empty\", \"args\": {}}. "
                 "Only use real tools: web_search, get_news, read_file, create_file, open_app, run_command. "
                 "Return ONLY the JSON array, no explanation."
             )},
            {"role": "user", "content": request},
        ]
        try:
            raw = self._router.chat_sync(messages, max_tokens=300)
            # Extract JSON array from response
            import re
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            print(f"[Agent] Plan generation error: {e}")
        return []
