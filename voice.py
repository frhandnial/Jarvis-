"""Ears (speech-to-text) and mouth (text-to-speech) for JARVIS."""
import asyncio
import os
import re
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# speech_recognition needs "pyaudio". PyAudio has no Python 3.14 build yet, so use its
# drop-in fork PyAudioWPatch under the same name.
try:
    import pyaudio  # noqa: F401
except ImportError:
    import pyaudiowpatch
    sys.modules["pyaudio"] = pyaudiowpatch

# British male voice - fits JARVIS. Other options: en-GB-ThomasNeural, en-US-GuyNeural
VOICE = os.getenv("JARVIS_VOICE", "en-GB-RyanNeural")

_recognizer = None
_offline_engine = None


# ---------------- Mouth ----------------

def speak(text):
    """Speak text. Sentences are generated in parallel, so the first one plays
    while the rest are still being prepared."""
    print("JARVIS: " + text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(_generate_edge, s) for s in sentences]
        for sentence, job in zip(sentences, jobs):
            try:
                _play(job.result())
            except Exception as e:
                # No internet or edge-tts problem -> fall back to the built-in Windows voice
                print("  (edge-tts failed: {} - using offline voice)".format(e))
                _speak_offline(sentence)


def _generate_edge(text):
    import edge_tts

    path = os.path.join(tempfile.gettempdir(), "jarvis_{}.mp3".format(uuid.uuid4().hex))

    async def _save():
        await edge_tts.Communicate(text, VOICE).save(path)

    asyncio.run(_save())
    return path


def _play(path):
    import pygame

    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        time.sleep(0.05)
    pygame.mixer.music.unload()
    try:
        os.remove(path)
    except OSError:
        pass


def _speak_offline(text):
    global _offline_engine
    import pyttsx3
    if _offline_engine is None:
        _offline_engine = pyttsx3.init()
        _offline_engine.setProperty("rate", 180)
    _offline_engine.say(text)
    _offline_engine.runAndWait()


# ---------------- Ears ----------------

def microphone_available():
    try:
        import speech_recognition as sr
        return len(sr.Microphone.list_microphone_names()) > 0
    except Exception:
        return False


def listen(prompt="Listening..."):
    """Listen for one phrase and return it as text ('' if nothing understood)."""
    global _recognizer
    import speech_recognition as sr

    if _recognizer is None:
        _recognizer = sr.Recognizer()
        _recognizer.pause_threshold = 0.6       # seconds of silence = "you finished talking"
        _recognizer.non_speaking_duration = 0.3
        with sr.Microphone() as source:
            print("Calibrating microphone for background noise...")
            _recognizer.adjust_for_ambient_noise(source, duration=1)

    with sr.Microphone() as source:
        print(prompt)
        try:
            audio = _recognizer.listen(source, timeout=10, phrase_time_limit=15)
        except sr.WaitTimeoutError:
            return ""

    try:
        return _recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        print("  (speech recognition error: {})".format(e))
        return ""
