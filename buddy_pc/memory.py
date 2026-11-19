"""'Remember that...', 'forget that...': the same commands as the desk Buddy
(a port of components/assist/Memory.cpp in ecstoneusps/Claude-gadget), on
the notes both bodies share."""

import re

HEADER = ("# Things Buddy remembers, one per line. Edit or delete lines freely;\n"
          "# lines starting with # are ignored.\n")

_LEAD = ("hey buddy", "hi buddy", "okay buddy", "ok buddy", "buddy", "hey jarvis", "okay", "ok", "so", "and",
         "please", "hey")
_REMEMBER = ("remember that ", "don't forget that ", "dont forget that ", "do not forget that ", "make a note that ",
             "take a note that ", "note that ", "remember this ", "remember ", "don't forget ", "dont forget ",
             "make a note ", "take a note ")
_FORGET_ALL = {"forget everything", "forget everything you know", "forget everything you remember",
               "forget all of it", "erase your memory", "clear your memory"}
_FORGET_LAST = {"forget that", "forget it", "never mind that", "forget what i just said", "forget the last thing",
                "forget what i just told you"}


def _strip_fillers(s):
    s = s.strip()
    again = True
    while again:
        again = False
        low = s.lower()
        for w in _LEAD:
            if low.startswith(w) and (len(low) == len(w) or not low[len(w)].isalpha()):
                s = s[len(w):].lstrip(" ,.")
                again = True
                break
    return s.rstrip(" .!,")


def parse_command(heard):
    """(kind, fact): kind is "remember", "forget", "forget_last",
    "forget_all", or None (not a memory command)."""
    if not heard.strip() or heard.strip().endswith("?"):
        return None, ""  # questions go to the AI
    s = _strip_fillers(heard.replace("’", "'"))
    low = s.lower()
    if low in _FORGET_ALL:
        return "forget_all", ""
    if low in _FORGET_LAST:
        return "forget_last", ""
    for p in ("forget that ", "forget about ", "forget ", "please forget "):
        if low.startswith(p):
            return "forget", s[len(p):].strip()
    for p in _REMEMBER:
        if low.startswith(p):
            fact = s[len(p):].strip().lstrip(",:").strip()
            lf = fact.lower()
            # "remember when we...", "remember to ..." (a reminder) aren't notes.
            if len(fact) < 3 or lf.startswith(("when ", "to ")):
                return None, ""
            return "remember", fact
    return None, ""


_SWAP = {"i'm": "you're", "i am": "you are", "i've": "you've", "i'll": "you'll", "i'd": "you'd",
         "myself": "yourself", "mine": "yours", "my": "your", "me": "you", "i": "you", "i was": "you were"}


def to_second_person(fact):
    """'my dog is King' -> 'your dog is King' (for saying it back)."""
    tokens = re.findall(r"[A-Za-z']+|[^A-Za-z']+", fact)
    out, k = [], 0
    while k < len(tokens):
        t = tokens[k]
        if not (t[0].isalpha() or t[0] == "'"):
            out.append(t)
            k += 1
            continue
        if k + 2 < len(tokens) and tokens[k + 1] == " ":
            two = (t + " " + tokens[k + 2]).lower()
            if two in _SWAP:
                out.append(_SWAP[two])
                k += 3
                continue
        out.append(_SWAP.get(t.lower(), t) if " " not in t else t)
        k += 1
    return "".join(out)


def _key(s):
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch == "'":
            continue
        elif out and out[-1] != " ":
            out.append(" ")
    return "".join(out).strip()


_SKIP = {"my", "the", "a", "an", "is", "are", "i", "that", "of", "to", "and"}


def _words(s):
    return {w for w in _key(s).split() if w not in _SKIP}


def to_text(notes):
    return HEADER + "".join(n + "\n" for n in notes)


def apply(kind, fact, notes):
    """Carries out a memory command on a list of notes. Returns (new notes,
    what to say with an emotion tag, changed?)."""
    notes = list(notes)
    if kind == "remember":
        if any(_key(n) == _key(fact) for n in notes):
            return notes, f"[winking] I already knew that, {to_second_person(fact)}!", False
        notes.append(fact)
        return notes, f"[happy] Got it! I'll remember that {to_second_person(fact)}.", True
    if kind == "forget_last":
        if not notes:
            return notes, "[confused] There's nothing to forget yet!", False
        gone = notes.pop()
        return notes, f"[thinking] Okay, I forgot that {to_second_person(gone)}.", True
    if kind == "forget":
        if not notes:
            return notes, "[confused] I don't have anything written down yet!", False
        want = _words(fact)
        best, best_shared = -1, 0
        for i, n in enumerate(notes):
            shared = len(want & _words(n))
            if shared > 0 and shared >= best_shared:  # newer notes win ties
                best, best_shared = i, shared
        if best < 0 or best_shared * 2 < len(want):
            return notes, "[confused] Hmm, I don't remember anything like that.", False
        gone = notes.pop(best)
        return notes, f"[thinking] Okay, I forgot that {to_second_person(gone)}.", True
    if kind == "forget_all":
        if not notes:
            return notes, "[confused] My notes are already empty!", False
        return [], "[sad] Okay, my notes are wiped clean. I kept a backup on the PC, just in case.", True
    return notes, "", False
