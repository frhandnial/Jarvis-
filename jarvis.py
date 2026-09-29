"""
JARVIS - a voice assistant powered by Google Gemini (free tier).

Run:
    python jarvis.py          # voice mode (say "Jarvis ..." to wake him)
    python jarvis.py --text   # type instead of talking
"""
import datetime
import os
import sys

from dotenv import load_dotenv

import brain
import voice
import memory
import tools

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------- Settings ----------------
MODEL = os.getenv("JARVIS_MODEL", "gemini-flash-lite-latest")
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
CITY = os.getenv("JARVIS_CITY", "").strip()  # empty = detect from internet connection
WAKE_WORD = "jarvis"
# ------------------------------------------

SYSTEM_PROMPT = """You are JARVIS, a witty, loyal and highly capable British AI assistant, \
inspired by Tony Stark's assistant. Address the user as "sir" occasionally, but not in every sentence.

Your replies are SPOKEN ALOUD, so:
- Keep answers short (1-3 sentences) unless asked for detail.
- No markdown, bullet points, emojis, code blocks or URLs in replies.
- Write numbers and symbols the way they should be spoken.

Use your tools when they help (opening apps, websites, weather, time, notes, remembering things).
If the user tells you something personal worth remembering (name, preferences, birthdays), \
save it with the remember tool.

Things you remember about the user:
{memories}
"""


def build_system_prompt():
    facts = memory.load()
    text = "\n".join("- " + f for f in facts) if facts else "(nothing yet)"
    return SYSTEM_PROMPT.format(memories=text)


def startup_briefing():
    """Greeting with the time, day and weather - built locally so it's instant and free."""
    now = datetime.datetime.now()
    if now.hour < 12:
        greeting = "Good morning, sir."
    elif now.hour < 18:
        greeting = "Good afternoon, sir."
    else:
        greeting = "Good evening, sir."

    time_text = now.strftime("%I:%M %p").lstrip("0")
    date_text = "{} the {}".format(now.strftime("%A"), ordinal(now.day)) + now.strftime(" of %B")
    parts = [greeting, "It is {} on {}.".format(time_text, date_text)]

    try:
        city, w = tools.fetch_weather(CITY, timeout=6)
        parts.append("The weather in {} is {}, at {} degrees Celsius, feeling like {}.".format(
            city, w["weatherDesc"][0]["value"].strip().lower(), w["temp_C"], w["FeelsLikeC"]))
    except Exception:
        parts.append("I'm unable to fetch the weather at the moment.")

    parts.append("How may I assist you?")
    return " ".join(parts)


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "{}{}".format(n, suffix)


def is_goodbye(text):
    t = text.lower()
    return any(w in t for w in ("goodbye", "shut down", "shutdown", "exit", "go to sleep jarvis"))


_instance_lock = None


def already_running():
    """Only one JARVIS at a time: holding this local port marks him as running."""
    global _instance_lock
    import socket
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(("127.0.0.1", 47913))
        return False
    except OSError:
        return True


def main():
    if already_running():
        print("JARVIS is already running.")
        return

    if not API_KEY:
        print("ERROR: No API key found.")
        print("Open the .env file in this folder and paste your key after GEMINI_API_KEY=")
        print("Get a free key at https://aistudio.google.com/apikey")
        input("Press Enter to exit...")
        return

    text_mode = "--text" in sys.argv
    if not text_mode and not voice.microphone_available():
        print("No microphone detected - switching to text mode.")
        text_mode = True

    history = []

    print("=" * 50)
    print("  J.A.R.V.I.S. online   (model: {})".format(MODEL))
    if text_mode:
        print("  Text mode - type your message. Type 'goodbye' to quit.")
    else:
        print("  Voice mode - start with \"Jarvis\", e.g. \"Jarvis, what time is it?\"")
        print("  Say \"goodbye\" to quit. Press Ctrl+C to force quit.")
    print("=" * 50)

    voice.speak(startup_briefing())

    awake = False  # after waking, JARVIS listens for follow-ups without the wake word
    while True:
        try:
            if text_mode:
                user_text = input("\nYou: ").strip()
            else:
                user_text = voice.listen(prompt="Listening..." if awake else "Waiting for 'Jarvis'...")
                if not user_text:
                    awake = False
                    continue
                lower = user_text.lower()
                if not awake and WAKE_WORD not in lower:
                    continue
                if WAKE_WORD in lower:
                    # remove the wake word so "Jarvis, open notepad" -> "open notepad"
                    idx = lower.index(WAKE_WORD)
                    user_text = (user_text[:idx] + user_text[idx + len(WAKE_WORD):]).strip(" ,.!?")
                    if not user_text:
                        awake = True
                        voice.speak("Yes, sir?")
                        continue
                print("You: " + user_text)

            if not user_text:
                continue

            if is_goodbye(user_text):
                voice.speak("Goodbye, sir. JARVIS signing off.")
                break

            reply = brain.ask(MODEL, API_KEY, build_system_prompt(), history, user_text)
            if reply:
                voice.speak(reply)
            awake = True

        except KeyboardInterrupt:
            print("\nShutting down.")
            break
        except brain.BrainError as e:
            print("ERROR: {}".format(e))
            voice.speak("I'm having trouble reaching my servers, sir.")


if __name__ == "__main__":
    main()
