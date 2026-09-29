# JARVIS

A voice assistant powered by Google Gemini (free tier).

## First-time setup
1. Get a free API key at https://aistudio.google.com/apikey
2. Copy `.env.example` to `.env`, open it and paste the key after `GEMINI_API_KEY=`.
3. (Only needed if the `venv` folder is missing) Double-click `setup.bat`.

## Run
- `run.bat`: voice mode. Start with "Jarvis...", e.g. *"Jarvis, what's the weather in London?"*
  After he answers, you can ask follow-ups without saying "Jarvis" again.
- `run-text-mode.bat`: type instead of talk.
- Say or type **"goodbye"** to quit.

## What he can do
- Chat about and answer anything
- Tell the time and date
- Open apps ("open notepad", "open spotify")
- Open websites and search Google or YouTube
- Check the weather
- Remember things about you (`memory.json`)
- Take and read notes (`notes.txt`)

## Files
| File | What it does |
|---|---|
| `jarvis.py` | Main loop and personality (edit `SYSTEM_PROMPT` to change him) |
| `brain.py` | Talks to Gemini and runs the tools it asks for |
| `voice.py` | Listening (Google speech recognition) and speaking (edge-tts) |
| `tools.py` | Actions he can take. Add new ones here |
| `memory.py` | Long-term memory |

## Change the voice
Set `JARVIS_VOICE` in `.env`, e.g. `en-GB-ThomasNeural` or `en-US-GuyNeural`.
List all voices: `venv\Scripts\edge-tts --list-voices`
