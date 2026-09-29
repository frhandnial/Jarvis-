"""Long-term memory: facts about the user saved to memory.json."""
import json
import os

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")


def load():
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def add(fact):
    facts = load()
    if fact not in facts:
        facts.append(fact)
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(facts, f, indent=2)


def forget(keyword):
    facts = load()
    kept = [f for f in facts if keyword.lower() not in f.lower()]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2)
    return len(facts) - len(kept)
