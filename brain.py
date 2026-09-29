"""JARVIS's brain: Google Gemini (free tier), called directly over its REST API."""
import json
import os
import urllib.error
import urllib.request

import tools

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_HISTORY = 40  # messages kept in the conversation


class BrainError(Exception):
    pass


class Overloaded(BrainError):
    pass


def _function_declarations():
    """Convert tools.TOOL_DEFINITIONS into Gemini's format."""
    decls = []
    for t in tools.TOOL_DEFINITIONS:
        d = {"name": t["name"], "description": t["description"]}
        schema = t["input_schema"]
        if schema.get("properties"):  # Gemini rejects empty object schemas
            d["parameters"] = schema
        decls.append(d)
    return decls


def _is_user_text(msg):
    return msg["role"] == "user" and any("text" in p for p in msg["parts"])


def _trim(history):
    while len(history) > MAX_HISTORY:
        history.pop(0)
    while history and not _is_user_text(history[0]):
        history.pop(0)


# If one model is overloaded, try the other
FALLBACKS = {"gemini-flash-lite-latest": "gemini-flash-latest"}
DEFAULT_FALLBACK = "gemini-flash-lite-latest"


def _call(model, api_key, system_prompt, history):
    try:
        return _call_once(model, api_key, system_prompt, history)
    except Overloaded:
        fallback = FALLBACKS.get(model, DEFAULT_FALLBACK)
        if fallback != model:
            print("  ({} is busy - using {})".format(model, fallback))
            return _call_once(fallback, api_key, system_prompt, history)
        raise


def _call_once(model, api_key, system_prompt, history):
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": history,
        "tools": [{"function_declarations": _function_declarations()}],
    }
    req = urllib.request.Request(
        API_URL.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if e.code in (500, 503):
            raise Overloaded("Gemini's servers are busy right now. Try again in a minute.")
        if e.code == 429:
            raise BrainError("Free-tier limit reached. Wait a minute (or until tomorrow) and try again.")
        if e.code in (400, 403) and "API key" in detail:
            raise BrainError("Gemini rejected the API key. Check GEMINI_API_KEY in the .env file.")
        raise BrainError("Gemini error {}: {}".format(e.code, detail[:300]))
    except urllib.error.URLError as e:
        raise BrainError("Can't reach Gemini (no internet?): {}".format(e.reason))


def ask(model, api_key, system_prompt, history, user_text):
    """Send user_text, run any tools Gemini asks for, and return the spoken reply."""
    history.append({"role": "user", "parts": [{"text": user_text}]})
    _trim(history)

    for _ in range(8):  # safety limit on tool-call rounds
        data = _call(model, api_key, system_prompt, history)
        candidates = data.get("candidates") or []
        if not candidates or "content" not in candidates[0]:
            return "I'm afraid I can't answer that one, sir."

        content = candidates[0]["content"]
        content.setdefault("role", "model")
        parts = content.get("parts", [])
        history.append(content)  # keep as-is (includes any thought signatures)

        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()

        responses = []
        for call in calls:
            args = call.get("args") or {}
            print("  [tool] {}({})".format(call["name"], args))
            result = tools.run_tool(call["name"], args)
            responses.append({"functionResponse": {"name": call["name"], "response": {"result": result}}})
        history.append({"role": "user", "parts": responses})

    return "That took more steps than I expected, sir. Could you ask again?"
