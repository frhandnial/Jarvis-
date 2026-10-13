"""
JARVIS - a voice assistant powered by Google Gemini (free tier).

Run:
    pythonw jarvis.py          # window + voice (default; say "Jarvis ..." to wake him)
    python jarvis.py --console # voice only, no window
    python jarvis.py --text    # type in the console instead of talking
"""
import datetime
import os
import socket
import sys
import threading
import time

from dotenv import load_dotenv

import brain
import events
import voice
import memory
import timetable
import tools

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

# ---------------- Settings ----------------
MODEL = os.getenv("JARVIS_MODEL", "gemini-flash-lite-latest")
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
CITY = os.getenv("JARVIS_CITY", "").strip()  # empty = detect from internet connection
WAKE_WORD = "jarvis"
IDLE_SECONDS = float(os.getenv("JARVIS_IDLE_MINUTES", "5")) * 60  # voice mode: close after this long unused
SLEEP_GAP_SECONDS = 90  # a single listen lasting longer than this means the laptop was asleep
INSTANCE_PORT = 47913   # held by the running JARVIS so a second copy never starts
# ------------------------------------------

SYSTEM_PROMPT = """You are JARVIS, a witty, loyal and highly capable British AI assistant, \
inspired by Tony Stark's assistant. Address the user as "sir" occasionally, but not in every sentence.

Your replies are SPOKEN ALOUD, so:
- Keep answers short (1-3 sentences) unless asked for detail.
- No markdown, bullet points, emojis, code blocks or URLs in replies.
- Write numbers and symbols the way they should be spoken.

Use your tools when they help (opening apps, websites, weather, time, notes, remembering things,
the user's university class timetable, files and folders). Class times are UK time; if the tool also gives a local time, mention both.
To open a file or folder the user names: call find_files first, then open_path with the best match's full path. \
If several results match about equally well, briefly ask which one (say names, not full paths). \
For "newest" or "latest" files, use recent_files. Never just open File Explorer and tell the user to look themselves.
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

    try:
        classes = timetable.briefing()
        if classes:
            parts.append(classes)
    except Exception as e:
        print("  (could not read timetable: {})".format(e))

    parts.append("How may I assist you?")
    return " ".join(parts)


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "{}{}".format(n, suffix)


def is_goodbye(text):
    t = text.lower()
    return any(w in t for w in ("goodbye", "shut down", "shutdown", "exit", "go to sleep jarvis"))


class Assistant:
    """Conversation state shared by the microphone loop and the typing box."""

    def __init__(self):
        self.history = []
        self.lock = threading.RLock()      # one command at a time
        self.last_active = time.monotonic()
        self.last_spoke = 0.0              # when JARVIS last finished talking
        self.awake = False                 # after waking, listen for follow-ups without the wake word
        self.muted = False
        self.stopped = threading.Event()

    def status(self, state):
        events.emit("status", "muted" if self.muted and state in ("standby", "listening") else state)

    def say(self, text):
        events.emit("jarvis", text)
        self.status("speaking")
        try:
            voice.speak(text)
        finally:
            self.last_spoke = time.monotonic()
            self.status("standby")

    def handle(self, user_text):
        """Run one command (spoken or typed)."""
        with self.lock:
            events.emit("user", user_text)
            self.last_active = time.monotonic()
            if is_goodbye(user_text):
                self.say("Goodbye, sir. JARVIS signing off.")
                self.stop()
                return
            self.status("thinking")
            try:
                reply = brain.ask(MODEL, API_KEY, build_system_prompt(), self.history, user_text)
                if reply:
                    self.say(reply)
            except brain.BrainError as e:
                print("ERROR: {}".format(e))
                events.emit("error", str(e))
                self.say("I'm having trouble reaching my servers, sir.")
            self.status("standby")
            self.awake = True
            self.last_active = time.monotonic()

    def stop(self):
        self.stopped.set()
        events.emit("quit")

    def voice_loop(self):
        while not self.stopped.is_set():
            if time.monotonic() - self.last_active > IDLE_SECONDS:
                self.say("No one has called for me in a while. Shutting down, sir.")
                self.stop()
                return

            started = time.monotonic()
            if self.muted:
                self.status("muted")
                time.sleep(0.5)
                user_text = ""
            else:
                self.status("listening" if self.awake else "standby")
                user_text = voice.listen(prompt="Listening..." if self.awake else "Waiting for 'Jarvis'...")
            if self.stopped.is_set():
                return
            if time.monotonic() - started > SLEEP_GAP_SECONDS:
                # One pass can't take this long, so the laptop was asleep - that time isn't idle time
                self.say(startup_briefing())
                self.last_active = time.monotonic()
                self.awake = False
                continue
            if self.last_spoke > started:
                continue  # JARVIS talked (typed command) while we listened: the mic may have heard him
            if not user_text:
                self.awake = False
                continue
            lower = user_text.lower()
            if not self.awake and WAKE_WORD not in lower:
                continue
            if WAKE_WORD in lower:
                # remove the wake word so "Jarvis, open notepad" -> "open notepad"
                idx = lower.index(WAKE_WORD)
                user_text = (user_text[:idx] + user_text[idx + len(WAKE_WORD):]).strip(" ,.!?")
                if not user_text:
                    self.awake = True
                    self.last_active = time.monotonic()
                    self.say("Yes, sir?")
                    continue
            print("You: " + user_text)
            self.handle(user_text)

    def text_loop(self):
        while not self.stopped.is_set():
            user_text = input("\nYou: ").strip()
            if user_text:
                self.handle(user_text)


# ---------------- Single instance ----------------
_instance_sock = None


def claim_instance():
    """True if we are the only JARVIS. Otherwise ask the running one to show its window."""
    global _instance_sock
    _instance_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_sock.bind(("127.0.0.1", INSTANCE_PORT))
        _instance_sock.listen(1)
        return True
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as s:
                s.sendall(b"show")
        except OSError:
            pass
        return False


def serve_instance_requests():
    """Another launch (desktop icon, wake from sleep) asks us to bring the window forward."""
    while True:
        try:
            conn, _ = _instance_sock.accept()
            conn.close()
            events.emit("show")
        except OSError:
            return


# ---------------- Entry points ----------------
def run_console(text_mode):
    if not API_KEY:
        print("ERROR: No API key found. Paste your key after GEMINI_API_KEY= in the .env file.")
        print("Get a free key at https://aistudio.google.com/apikey")
        input("Press Enter to exit...")
        return
    if not text_mode and not voice.microphone_available():
        print("No microphone detected - switching to text mode.")
        text_mode = True
    a = Assistant()
    print("=" * 50)
    print("  J.A.R.V.I.S. online   (model: {})".format(MODEL))
    print("  Type 'goodbye' to quit." if text_mode else '  Start with "Jarvis". Say "goodbye" to quit.')
    print("=" * 50)
    a.say(startup_briefing())
    try:
        a.text_loop() if text_mode else a.voice_loop()
    except KeyboardInterrupt:
        print("\nShutting down.")


def run_gui():
    import gui

    # pythonw has no console: keep a log for troubleshooting
    log = open(os.path.join(HERE, "jarvis.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    print("\n=== JARVIS started {} ===".format(datetime.datetime.now()))

    events.enabled = True
    a = Assistant()
    threading.Thread(target=serve_instance_requests, daemon=True).start()

    def run():
        if not API_KEY:
            events.emit("error", "No API key. Paste your Gemini key after GEMINI_API_KEY= in the .env file "
                                 "(free at aistudio.google.com/apikey), then restart JARVIS.")
            return
        a.say(startup_briefing())
        if voice.microphone_available():
            a.voice_loop()
        else:
            events.emit("error", "No microphone found - type your commands below.")
            a.muted = True
            a.voice_loop()  # still handles the idle timer and sleep detection

    threading.Thread(target=run, daemon=True).start()
    gui.Hud(a, MODEL).mainloop()
    os._exit(0)  # stop the listening thread immediately


def main():
    if not claim_instance():
        print("JARVIS is already running.")
        return
    tools.warm_index()
    if "--text" in sys.argv:
        run_console(text_mode=True)
    elif "--console" in sys.argv:
        run_console(text_mode=False)
    else:
        run_gui()


if __name__ == "__main__":
    main()
