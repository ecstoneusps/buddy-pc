"""Who's talking: the voice ID service the desk Buddy uses (tools/voiceid in
ecstoneusps/Claude-gadget) compares the voice with the owner's voiceprint."""

import requests

LINES = {
    "owner": "[Speaking now: your owner.]\n",
    "other": "[Speaking now: someone who is not your owner (a guest). Don't call them by your owner's name; you "
             "don't know their name unless they tell you.]\n",
    "unsure": "[You can't tell who is speaking this time, so don't call them by name.]\n",
}


def identify(cfg, pcm, log):
    """'owner', 'other', 'unsure', or None (off, not set up or not reachable)."""
    url = cfg.get("voice_id_url")
    if not url or len(pcm) < 16000 * 1.2:  # too short to tell
        return None
    try:
        r = requests.post(f"{url}/identify", data=pcm.astype("<i2").tobytes(),
                          headers={"Content-Type": "application/octet-stream"}, timeout=5)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        log(f"(voice ID didn't answer: {type(e).__name__})", debug=True)
        return None
    if not d.get("enrolled"):
        return None
    log(f"voice: {d.get('decision')} (score {d.get('score', 0):.2f}, {d.get('seconds', 0):.1f} s)")
    return d.get("decision")
