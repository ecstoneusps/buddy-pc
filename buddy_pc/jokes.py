"""Different jokes: asked for "a joke", a small model tells the same one
every time (the skeletons with no guts). He remembers the ones he's told
(jokes.json) and asks for a new one, on a topic picked at random."""

import json
import os
import random
import re

from .config import HERE

FILE = os.path.join(HERE, "jokes.json")
TOPICS = ("animals", "space", "food", "computers", "music", "sports", "the weather", "history", "science",
          "dogs", "robots", "video games", "cooking", "the ocean", "school", "cars", "math", "dinosaurs",
          "pirates", "birds", "trees and plants", "the office", "doctors", "superheroes", "outer space aliens",
          "camping", "holidays", "monsters", "magic", "trains")


def wanted(heard):
    return bool(re.search(r"\b(joke|jokes|pun|something funny|make me laugh)\b", heard, re.I))


def _load():
    try:
        with open(FILE, encoding="utf-8") as f:
            return list(json.load(f))
    except (OSError, ValueError):
        return []


def told(text):
    """Remembers a joke he told (the last 40)."""
    lst = _load()
    t = text.strip()
    if t and t not in lst:
        lst.append(t)
        try:
            with open(FILE, "w", encoding="utf-8") as f:
                json.dump(lst[-40:], f, indent=1)
        except OSError:
            pass


def hint(topic=None):
    recent = _load()[-12:]
    topic = topic or random.choice(TOPICS)
    avoid = (" Not one of these, which you've told before: " + " | ".join(recent) + ".") if recent else ""
    return (f"[If you tell a joke, make it a fresh one, about {topic}, that you haven't told before.{avoid} "
            "Not the one about skeletons and guts.]\n")
