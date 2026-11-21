"""Text helpers shared with the desk Buddy (ports of components/assist in
ecstoneusps/Claude-gadget): emotion tags, sentence splitting for speaking
sooner, "cancel", and the memory notes block."""

import re

# ---------------------------------------------------------------------------
# Replies: "[laughing] Ha! ..." -> ("laughing", "Ha! ...")

_TAG = re.compile(r"\[([A-Za-z _-]{1,22})\]")
_THINK = re.compile(r"<think>.*?(</think>|$)", re.S)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def strip_think(text):
    return _THINK.sub("", text)


def clean_for_speech(text):
    """What's left to say out loud: no markdown, emoji or extra spaces."""
    t = _EMOJI.sub("", text)
    t = re.sub(r"[*_`#]+", "", t)
    t = t.replace("—", ", ").replace("–", ", ")  # dashes read badly
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def split_reply(reply):
    """(emotion or "", speech). The first [tag] is the emotion; every tag is
    removed from what's spoken."""
    s = strip_think(reply)
    emotion = ""
    m = _TAG.search(s)
    if m:
        emotion = m.group(1).replace(" ", "").lower()
    return emotion, clean_for_speech(_TAG.sub(" ", s))


def sentence_end(text, start=0):
    """Index just past the next complete sentence after `start`, or -1.
    Short bits ("Hi.") wait for more; very long runs break at a comma."""
    min_chars, long_chars = 12, 180
    n = len(text)
    letters = 0
    for i in range(start, n):
        c = text[i]
        if c.isalpha():
            letters += 1
        ends = c == "\n" or (c in ".!?" and i + 1 < n and text[i + 1] in " \n")
        if ends and letters >= 2 and i + 1 - start >= min_chars:
            end = i + 1
            while end < n and text[end] in " \n":
                end += 1
            return end
        if i + 1 - start >= long_chars:
            cut = text.rfind(", ", start, i + 1)
            if cut < 0 or cut < start + min_chars:
                cut = text.rfind(" ", start, i + 1)
            if cut > start + min_chars:
                end = cut + 1
                while end < n and text[end] == " ":
                    end += 1
                return end
    return -1


# ---------------------------------------------------------------------------
# What you said

def normalize(text):
    """Lower case letters and single spaces; apostrophes dropped (don't -> dont)."""
    out = []
    for ch in text.lower():
        if ch.isalpha():
            out.append(ch)
        elif ch == "'" or ch == "’":
            continue
        elif out and out[-1] != " ":
            out.append(" ")
    return "".join(out).strip()


_CANCEL_WHOLE = {normalize(p) for p in (
    "cancel", "cancer", "cancel that", "cancel it", "never mind", "nevermind", "stop", "stop listening",
    "ignore that", "ignore it", "ignore me", "not you", "not now", "nothing", "nobody", "no one",
    "nobody's talking to you", "no one's talking to you")}
_CANCEL_PART = [" " + normalize(p) + " " for p in (
    "wasn't talking to you", "was not talking to you", "not talking to you", "that wasn't me", "it wasn't me",
    "that was not me", "wasn't me talking", "that was the tv", "that was the video", "that was a video",
    "that was youtube", "that was the television", "it was the tv", "it was the video", "it was a video",
    "it was youtube", "talking to someone else", "you misheard", "i didn't say anything", "i didn't call you",
    "i didn't say your name")]


def is_cancel(heard):
    """'Cancel', 'never mind', 'I wasn't talking to you', 'that was the TV'..."""
    h = normalize(heard)
    for wake in ("hey buddy ", "hi buddy ", "okay buddy ", "ok buddy ", "buddy "):
        if h.startswith(wake):
            h = h[len(wake):]
    for tail in (" buddy", " please", " thanks", " thank you"):
        if h.endswith(tail) and len(h) > len(tail):
            h = h[: -len(tail)]
    if h in _CANCEL_WHOLE:
        return True
    # Starting with it ("Cancel. What do you want me to say?" -- the rest was
    # a video talking over you).
    if any(h.startswith(p + " ") for p in ("cancel", "never mind", "nevermind", "stop listening")):
        return True
    if len(h.split()) > 10:
        return False
    s = " " + h + " "
    return any(p in s for p in _CANCEL_PART)


_VISION = [" " + normalize(p) + " " for p in (
    "what do you see", "what can you see", "what are you seeing", "what is this", "what's this", "what is that",
    "what's that", "what am I holding", "what I'm holding", "look at this", "look at that", "take a look",
    "can you see", "do you see", "describe what", "read this", "can you read", "what color is this",
    "what colour is this", "what does this say", "who is this", "who's this", "how do I look",
    "what am I wearing", "what's in front of you", "what is in front of you", "how many fingers",
    "fingers am I holding", "am I holding up", "what am I showing", "look at me", "can you see me",
    "what do I look like", "do I look", "what is on my", "what's on my", "what color is my",
    "what colour is my", "what color am I", "see what I", "in my hand", "am I holding")]


def is_vision_request(heard):
    """A question he can only answer by looking ("what am I holding?")."""
    h = " " + normalize(heard) + " "
    return any(p in h for p in _VISION)


_CLARIFY = [" " + normalize(p) + " " for p in (
    "rephrase", "say it again", "say that again", "repeat that", "repeat it", "didn't catch",
    "didn't quite catch", "didn't quite get", "didn't get that", "not sure I understand", "not sure I caught",
    "not sure I follow", "not sure what you mean", "not sure what you're asking", "not sure I'm following",
    "don't understand", "didn't understand", "could you clarify", "can you clarify", "what do you mean",
    "come again", "pardon")]


def is_clarify(reply):
    """The reply only asks you to repeat yourself: don't keep listening."""
    r = " " + normalize(reply) + " "
    if len(r.split()) > 30:
        return False
    return any(p in r for p in _CLARIFY)


# ---------------------------------------------------------------------------
# Memory notes (the desk Buddy's memories.txt, in the owner's words)

_KEEP = {"can", "could", "will", "would", "should", "must", "might", "may", "shall", "used", "was", "went", "got",
         "had", "saw", "made", "took", "came", "said", "did", "bought", "found", "gave", "knew", "left", "lost",
         "met", "paid", "ran", "sold", "told", "thought", "won", "wrote", "also", "really", "always", "never",
         "often", "usually", "just", "still", "sometimes"}


def _third_person_verb(w):
    lw = w.lower()
    if not lw or lw in _KEEP or (len(lw) > 3 and lw.endswith("ed")):
        return w
    special = {"have": "has", "do": "does", "go": "goes", "don't": "doesn't", "dont": "doesn't"}
    if lw in special:
        return special[lw]
    if lw[-1] in "sxzo" or lw.endswith(("sh", "ch")):
        return w + "es"
    if lw[-1] == "y" and len(lw) > 1 and lw[-2] not in "aeiou":
        return w[:-1] + "ies"
    return w + "s"


def to_third_person(fact):
    """'My dog is King' -> 'Your owner's dog is King' (for the AI)."""
    tokens = re.findall(r"[A-Za-z']+|[^A-Za-z']+", fact)
    is_word = lambda t: bool(t) and (t[0].isalpha() or t[0] == "'")
    out = []
    named = False

    def owner(first, later):
        nonlocal named
        out.append(later if named else first)
        named = True

    k = 0
    while k < len(tokens):
        t = tokens[k]
        if not is_word(t):
            out.append(t)
            k += 1
            continue
        lw = t.lower()
        simple = {"my": ("your owner's", "their"), "mine": ("your owner's", "theirs"),
                  "myself": ("your owner", "themselves"), "me": ("your owner", "them"),
                  "i'm": ("your owner is", "they're"), "i've": ("your owner has", "they've"),
                  "i'll": ("your owner will", "they'll"), "i'd": ("your owner would", "they'd")}
        if lw in simple:
            owner(*simple[lw])
        elif lw == "i" and named:
            out.append("they")
        elif lw == "i":
            owner("your owner", "they")
            n = next((m for m in range(k + 1, len(tokens)) if is_word(tokens[m])), -1)
            if n >= 0:
                v = tokens[n].lower()
                verb = "is" if v == "am" else "was" if v == "was" else _third_person_verb(tokens[n])
                out.extend(tokens[k + 1:n])
                out.append(verb)
                k = n
        else:
            out.append(t)
        k += 1
    s = "".join(out)
    return s[:1].upper() + s[1:]


def parse_notes(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def notes_block(notes, max_chars=3000):
    """The desk Buddy's background-notes block, so both bodies know the same things."""
    if not notes:
        return ""
    picked, used = [], 0
    for n in reversed(notes):
        if used + len(n) + 4 > max_chars:
            break
        picked.append(n)
        used += len(n) + 4
    picked.reverse()
    return ("[Background notes about your owner, the person talking to you, for reference only: "
            + "; ".join(to_third_person(n) for n in picked)
            + ". When your owner says \"my\" or \"mine\" they mean themselves; \"your\" or \"yours\" means you, "
              "Buddy. Don't bring these up on your own. Use a note when your owner asks about it or the "
              "conversation is directly about that topic; an occasional natural mention is fine, but never "
              "work them into answers about something else.]\n")
