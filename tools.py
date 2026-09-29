"""Actions JARVIS can take. Add new tools here: a definition + a function + an entry in _HANDLERS."""
import datetime
import json
import os
import subprocess
import urllib.parse
import urllib.request
import webbrowser

import memory

NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes.txt")

# Friendly names -> Windows commands
APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "file explorer": "explorer",
    "explorer": "explorer",
    "command prompt": "cmd",
    "terminal": "wt",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "chrome": "chrome",
    "edge": "msedge",
    "vs code": "code",
    "vscode": "code",
    "spotify": "spotify:",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "camera": "microsoft.windows.camera:",
}

TOOL_DEFINITIONS = [
    {
        "name": "get_datetime",
        "description": "Get the current local date and time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "open_app",
        "description": "Open an application on this Windows PC. Known apps: " + ", ".join(sorted(APPS)),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name, e.g. 'notepad'"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_website",
        "description": "Open a website in the default browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "e.g. youtube.com"}},
            "required": ["url"],
        },
    },
    {
        "name": "search_web",
        "description": "Search Google or YouTube in the browser for the user to look at.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "site": {"type": "string", "enum": ["google", "youtube"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "remember",
        "description": "Save a fact about the user to long-term memory.",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "e.g. 'User's name is Farhan'"}},
            "required": ["fact"],
        },
    },
    {
        "name": "forget",
        "description": "Delete remembered facts containing a keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "write_note",
        "description": "Save a note or to-do item to the notes file.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "read_notes",
        "description": "Read back all saved notes.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def get_datetime():
    return datetime.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


def open_app(name):
    key = name.lower().strip()
    cmd = APPS.get(key, key)
    try:
        os.startfile(cmd)
    except OSError:
        subprocess.Popen('start "" "{}"'.format(cmd), shell=True)
    return "Opened " + name


def open_website(url):
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return "Opened " + url


def search_web(query, site="google"):
    q = urllib.parse.quote_plus(query)
    if site == "youtube":
        url = "https://www.youtube.com/results?search_query=" + q
    else:
        url = "https://www.google.com/search?q=" + q
    webbrowser.open(url)
    return "Searching {} for {}".format(site, query)


def fetch_weather(city="", timeout=10):
    """Current weather from wttr.in. An empty city means 'detect from my internet connection'."""
    url = "https://wttr.in/{}?format=j1".format(urllib.parse.quote(city))
    req = urllib.request.Request(url, headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not city:
        city = data["nearest_area"][0]["areaName"][0]["value"]
    return city, data["current_condition"][0]


def get_weather(city):
    city, now = fetch_weather(city)
    return "{}: {}, {}°C (feels like {}°C), humidity {}%, wind {} km/h".format(
        city,
        now["weatherDesc"][0]["value"],
        now["temp_C"],
        now["FeelsLikeC"],
        now["humidity"],
        now["windspeedKmph"],
    )


def remember(fact):
    memory.add(fact)
    return "Saved to memory."


def forget(keyword):
    n = memory.forget(keyword)
    return "Forgot {} fact(s).".format(n)


def write_note(text):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write("[{}] {}\n".format(stamp, text))
    return "Note saved."


def read_notes():
    if not os.path.exists(NOTES_FILE):
        return "No notes yet."
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        return f.read() or "No notes yet."


_HANDLERS = {
    "get_datetime": get_datetime,
    "open_app": open_app,
    "open_website": open_website,
    "search_web": search_web,
    "get_weather": get_weather,
    "remember": remember,
    "forget": forget,
    "write_note": write_note,
    "read_notes": read_notes,
}


def run_tool(name, args):
    try:
        return str(_HANDLERS[name](**args))
    except Exception as e:
        return "Error running {}: {}".format(name, e)
