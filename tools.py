"""Actions JARVIS can take. Add new tools here: a definition + a function + an entry in _HANDLERS."""
import datetime
import difflib
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import memory
import timetable

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

# Folders searched when looking for files (missing ones are skipped)
HOME = os.path.expanduser("~")
_FOLDER_CANDIDATES = {
    "desktop": ["OneDrive/Desktop", "Desktop"],
    "documents": ["OneDrive/Documents", "Documents"],
    "downloads": ["Downloads"],
    "pictures": ["OneDrive/Pictures", "Pictures"],
    "videos": ["Videos"],
    "music": ["Music"],
}
FOLDERS = {}
for _name, _subs in _FOLDER_CANDIDATES.items():
    _found = [os.path.join(HOME, *s.split("/")) for s in _subs if os.path.isdir(os.path.join(HOME, *s.split("/")))]
    if _found:
        FOLDERS[_name] = _found

SEARCH_MAX_DEPTH = 6
SEARCH_TIME_LIMIT = 10  # seconds (the index build may take up to 3x this)
SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", ".git", "site-packages", "appdata", "$recycle.bin"}

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
    {
        "name": "get_classes",
        "description": "Get the user's university classes (University of Sheffield timetable, UK time) for one day: "
                       "how many, times, module, activity, location and lecturer.",
        "input_schema": {
            "type": "object",
            "properties": {"day": {"type": "string",
                                   "description": "'today', 'tomorrow', 'yesterday', a weekday like 'friday' or "
                                                  "'next monday', or a date YYYY-MM-DD"}},
            "required": ["day"],
        },
    },
    {
        "name": "find_files",
        "description": "Search the user's Desktop, Documents, Downloads, Pictures, Videos and Music for files or "
                       "folders whose name roughly matches (handles misheard or partial names). Returns full paths, "
                       "best match first. Use this before open_path when the user names a file or folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or part of the name, e.g. '3D printer compositions'"},
                "kind": {"type": "string", "enum": ["any", "file", "folder"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_path",
        "description": "Open a file (with its default program) or a folder (in File Explorer) by its full path. "
                       "Also accepts 'desktop', 'documents', 'downloads', 'pictures', 'videos' or 'music'.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "recent_files",
        "description": "List the most recently modified files in a folder, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string",
                           "description": "'downloads', 'desktop', 'documents', 'pictures', 'videos', 'music' "
                                          "or a full folder path"},
                "count": {"type": "integer", "description": "How many to list (default 5)"},
            },
            "required": ["folder"],
        },
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


_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                  "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def _tokens(text):
    """'3D_Printer-Compositions.stl' -> ['3d', 'printer', 'composition']"""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()  # camelCase -> camel case
    words = [_NUMBER_WORDS.get(w, w) for w in re.findall(r"[a-z0-9]+", text)]
    joined = " ".join(words)
    joined = re.sub(r"\b(\d+) d\b", r"\1d", joined)  # "three d" / "3 d" -> "3d"
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in joined.split()]


def _name_tokens(name, is_dir):
    stem = name if is_dir else os.path.splitext(name)[0]
    return _tokens(stem)


def _match_score(query_tokens, name_tokens):
    """0..1: how well a file/folder name matches the spoken query."""
    if not name_tokens:
        return 0.0
    hits = 0.0
    for q in query_tokens:
        best = 0.0
        for n in name_tokens:
            if q == n:
                best = 1.0
                break
            if len(q) >= 3 and (n.startswith(q) or q.startswith(n)):
                best = max(best, 0.9)
            elif abs(len(q) - len(n)) <= max(len(q), len(n)) // 2 and q[0] == n[0]:
                sm = difflib.SequenceMatcher(None, q, n)
                if sm.quick_ratio() > best:
                    best = max(best, sm.ratio())
        if best >= 0.75:
            hits += best
    score = hits / len(query_tokens)
    if score < 0.5:  # can't reach the threshold, skip the expensive comparison
        return score
    whole = difflib.SequenceMatcher(None, " ".join(query_tokens), " ".join(name_tokens)).ratio()
    return 0.8 * score + 0.2 * whole


def _resolve_folder(folder):
    key = folder.lower().strip()
    if key in FOLDERS:
        return FOLDERS[key][0]
    return os.path.expandvars(os.path.expanduser(folder.strip().strip('"')))


# File index: walking ~100k files takes seconds, so build it once and reuse it for a while
INDEX_MAX_AGE = 600  # seconds
_index = {"built": 0.0, "entries": []}  # entries: (path, is_dir, depth, tokens)
_index_lock = threading.Lock()


def _build_index():
    entries = []
    deadline = time.monotonic() + SEARCH_TIME_LIMIT * 3
    for root in [p for paths in FOLDERS.values() for p in paths]:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                break
            depth = dirpath.count(os.sep) - base_depth
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in SKIP_DIRS and not d.startswith(".") and depth < SEARCH_MAX_DEPTH]
            for d in dirnames:
                entries.append((os.path.join(dirpath, d), True, depth, _name_tokens(d, True)))
            for f in filenames:
                if f.startswith(("~$", ".")) or f.lower() == "desktop.ini":
                    continue  # Office lock files and hidden files
                entries.append((os.path.join(dirpath, f), False, depth, _name_tokens(f, False)))
    return entries


def _get_index():
    with _index_lock:
        if time.monotonic() - _index["built"] > INDEX_MAX_AGE or not _index["entries"]:
            _index["entries"] = _build_index()
            _index["built"] = time.monotonic()
        return _index["entries"]


def warm_index():
    """Build the file index in the background so the first search is fast."""
    threading.Thread(target=_get_index, daemon=True).start()


def find_files(query, kind="any"):
    query_tokens = _tokens(query)
    if not query_tokens:
        return "Please give me a name to search for."
    query_firsts = {q[0] for q in query_tokens}
    results = []
    for path, is_dir, depth, name_tokens in _get_index():
        if (kind == "file" and is_dir) or (kind == "folder" and not is_dir):
            continue
        if not any(n[0] in query_firsts for n in name_tokens):
            continue  # quick reject: no word even starts with the same letter
        score = _match_score(query_tokens, name_tokens)
        if score >= 0.6:
            results.append((score - 0.02 * depth, score, is_dir, path))  # prefer shallow, user-made files
    if not results:
        return "No files or folders matching '{}' found.".format(query)
    results.sort(key=lambda r: -r[0])
    lines = ["{} {} (match {:.0%})".format("[folder]" if is_dir else "[file]", path, score)
             for _, score, is_dir, path in results[:5]]
    return "\n".join(lines)


def open_path(path):
    path = _resolve_folder(path)
    if not os.path.exists(path):
        return "Error: '{}' does not exist. Use find_files to get the correct path.".format(path)
    os.startfile(path)
    return "Opened " + path


def recent_files(folder, count=5):
    path = _resolve_folder(folder)
    if not os.path.isdir(path):
        return "Error: folder '{}' not found.".format(path)
    entries = []
    for e in os.scandir(path):
        try:
            if e.is_file() and not e.name.startswith(("~$", ".")) and e.name.lower() != "desktop.ini":
                entries.append((e.stat().st_mtime, e.path))
        except OSError:
            pass
    if not entries:
        return "No files in " + path
    entries.sort(reverse=True)
    lines = ["{}  {}".format(datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"), p)
             for t, p in entries[:int(count)]]
    return "\n".join(lines)


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
    "get_classes": timetable.get_classes,
    "find_files": find_files,
    "open_path": open_path,
    "recent_files": recent_files,
}


def run_tool(name, args):
    try:
        return str(_HANDLERS[name](**args))
    except Exception as e:
        return "Error running {}: {}".format(name, e)
