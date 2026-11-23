"""Your home's devices, through Home Assistant's own assistant (the one the
desk Buddy talks through): "turn off the kitchen lights", "is the garage door
open?". Home Assistant matches the words to your devices and does it.

Needs ha_url and ha_token (a long-lived access token: Home Assistant, your
profile, Security, Long-lived access tokens) in buddy.ini. The token stays in
buddy.ini on this PC: it's never logged or shared."""

import re

import requests

# What a device command sounds like (anything else goes to his AI, which can
# still use the control_home tool for "it's dark in here").
_COMMAND = re.compile(
    r"\b(turn|switch|shut|put|flip)\b.{0,40}\b(on|off|up|down)\b"
    r"|\b(dim|brighten|lower|raise)\b.{0,30}\b(lights?|lamps?|brightness|blinds?|shades?|volume)\b"
    r"|\bset\b.{0,40}\b(to|at)\b.{0,20}(\d|percent|degrees|%)"
    r"|\b(lock|unlock|open|close)\b.{0,25}\b(doors?|garage|blinds?|shades?|curtains?|gate|windows?)\b"
    r"|\b(is|are)\b.{0,30}\b(lights?|lamps?|doors?|garage|fans?|tv|switch(es)?)\b.{0,15}\b(on|off|open|closed|locked|unlocked)\b"
    r"|\bwhat('s| is)\b.{0,15}\b(temperature|humidity)\b.{0,10}\b(in|inside|upstairs|downstairs)\b",
    re.I)


def ready(cfg):
    return bool(cfg.get("ha_url") and cfg.get("ha_token"))


def sounds_like_command(heard):
    return bool(_COMMAND.search(heard or ""))


def command(cfg, text, log):
    """(did it?, what Home Assistant said): done for a device it changed or a
    state it read; not done if it didn't understand which device you meant."""
    body = {"text": text, "language": cfg.get("language") or "en"}
    if cfg.get("ha_agent"):
        body["agent_id"] = cfg["ha_agent"]
    r = requests.post(f"{cfg['ha_url']}/api/conversation/process", json=body, timeout=15,
                      headers={"Authorization": f"Bearer {cfg['ha_token']}"})
    if r.status_code == 401:
        log("Home Assistant said the token isn't valid (ha_token in buddy.ini)")
        return False, "Home Assistant didn't accept my key."
    r.raise_for_status()
    resp = r.json().get("response", {})
    said = (((resp.get("speech") or {}).get("plain") or {}).get("speech") or "").strip()
    done = resp.get("response_type") in ("action_done", "query_answer")
    log(f"Home Assistant: \"{text}\" -> {resp.get('response_type')}: \"{said}\"")
    return done, said
