"""Buddy's memory notes, shared by both bodies.

The shared copy is a retained message on Home Assistant's MQTT broker
(buddy/<desk buddy>/memories: {"version", "from", "notes"}), so it's there
whenever either body asks, even when the other one is off. Each change gets
a higher version and the highest wins. A copy is also kept here
(memories.json), for when the broker can't be reached.
"""

import json
import os
import threading

from .config import HERE
from .textutil import parse_notes

CACHE_FILE = os.path.join(HERE, "memories.json")
STATUS_TOPIC = "buddy_pc/status"     # online / offline
WAKE_TOPIC = "buddy_pc/wake_word"    # what he listens for


class SharedNotes:
    def __init__(self, cfg, log, cache_file=CACHE_FILE):
        self.cfg, self.log, self.cache_file = cfg, log, cache_file
        self.version, self.text = 0, ""
        self.topic = None  # buddy/<node>/memories, once known
        self._lock = threading.Lock()
        self._load_cache()
        self.client = None
        if cfg.get("mqtt_host"):
            self._connect()
        else:
            log("not sharing memory notes with the desk Buddy (mqtt_host is empty in buddy.ini)")

    # -- the copy on this PC ----------------------------------------------------

    def _load_cache(self):
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                d = json.load(f)
            self.version, self.text = int(d.get("version", 0)), d.get("notes", "")
        except (OSError, ValueError):
            pass

    def _save_cache(self):
        tmp = self.cache_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": self.version, "notes": self.text}, f, indent=1)
            os.replace(tmp, self.cache_file)
        except OSError as e:
            self.log(f"couldn't save {self.cache_file}: {e}")

    def notes(self):
        with self._lock:
            return parse_notes(self.text)

    def apply(self, payload, topic=None):
        """A shared copy arrived: use it if it's newer. True if it was."""
        try:
            d = json.loads(payload)
            version, text = int(d["version"]), str(d["notes"])
        except (ValueError, KeyError, TypeError):
            return False
        with self._lock:
            if topic:
                self.topic = topic
            if version < self.version and self.client and self.topic:
                # Changed here while the broker was out of reach: ours is newer.
                payload = json.dumps({"version": self.version, "from": "pc", "notes": self.text})
                self.client.publish(self.topic, payload, qos=1, retain=True)
                self.log(f"shared the memory notes changed on the PC (version {self.version})")
                return False
            if version <= self.version:
                return False
            self.version, self.text = version, text
            self._save_cache()
        self.log(f"memory notes: version {version}, {len(parse_notes(text))} notes "
                 f"(from the {'desk Buddy' if d.get('from') == 'desk' else 'PC'})")
        return True

    # -- the shared copy -------------------------------------------------------

    def _connect(self):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="buddy_pc")
        if self.cfg.get("mqtt_user"):
            c.username_pw_set(self.cfg["mqtt_user"], self.cfg.get("mqtt_password") or None)
        # "Buddy on the PC is running": the desk Buddy lets him answer the wake
        # word first. If he stops without saying so, the broker says it for him.
        c.will_set(STATUS_TOPIC, "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_message = lambda client, userdata, msg: self.apply(msg.payload.decode("utf-8", "replace"), msg.topic)
        c.reconnect_delay_set(2, 60)
        c.connect_async(self.cfg["mqtt_host"], self.cfg.get("mqtt_port", 1883), 30)
        c.loop_start()
        self.client = c

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.log(f"MQTT login refused ({reason_code}): check mqtt_user / mqtt_password in buddy.ini")
            return
        client.subscribe("buddy/+/memories", qos=1)
        wake = self.cfg.get("wake_word") or "off"
        client.publish(WAKE_TOPIC, wake, qos=1, retain=True)
        client.publish(STATUS_TOPIC, "online", qos=1, retain=True)
        self.log("connected to MQTT (shared memory notes; "
                 + ("the desk Buddy lets me answer \"Hey Buddy\")" if "buddy" in wake.lower()
                    else "the desk Buddy keeps answering \"Hey Buddy\" until I listen for it too)"))

    def share(self, text):
        """Saves a change made here as the newest shared version."""
        with self._lock:
            self.version += 1
            self.text = text
            self._save_cache()
            payload = json.dumps({"version": self.version, "from": "pc", "notes": text})
            topic = self.topic
        if self.client and topic:
            self.client.publish(topic, payload, qos=1, retain=True)
            return True
        return False

    def close(self):
        """Buddy is quitting: tell the desk Buddy to answer again."""
        if self.client:
            try:
                self.client.publish(STATUS_TOPIC, "offline", qos=1, retain=True).wait_for_publish(2)
                self.client.disconnect()
                self.client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
