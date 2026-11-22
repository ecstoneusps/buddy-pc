"""What's playing on the PC (YouTube in Edge, Spotify...), through Windows'
media controls (the ones the keyboard's play/pause key uses): pause it while
you talk to him, resume it after, and know its title."""

import asyncio
import threading


class Media:
    def __init__(self, log):
        self.log = log
        self.ok = False
        self.paused = []  # sessions he paused, to resume
        try:
            from winrt.windows.media.control import \
                GlobalSystemMediaTransportControlsSessionManager as Manager  # noqa: F401
        except Exception as e:  # noqa: BLE001 (not Windows, or not installed)
            log(f"(no media controls: {type(e).__name__})", debug=True)
            return
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="media").start()
        self.ok = True

    def _run(self, coro, timeout=3.0):
        if not self.ok:
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)
        except Exception as e:  # noqa: BLE001 (media controls are a nice-to-have)
            self.log(f"(media controls: {type(e).__name__}: {e})", debug=True)
            return None

    @staticmethod
    async def _sessions():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        mgr = await Manager.request_async()
        return list(mgr.get_sessions())

    @staticmethod
    def _playing(session):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as St
        info = session.get_playback_info()
        return info is not None and info.playback_status == St.PLAYING

    def pause(self):
        """Pauses everything that's playing; remembers what, to resume it."""
        async def go():
            paused, names = [], []
            for s in await self._sessions():
                if self._playing(s):
                    p = await s.try_get_media_properties_async()
                    if await s.try_pause_async():
                        paused.append(s)
                        app = app_name(s.source_app_user_model_id)
                        title = f"\"{p.title}\"" if p and p.title else ""
                        names.append(" in ".join(x for x in (title, app) if x) or "something")
            return paused, names
        got = self._run(go())
        if got and got[0]:
            self.paused += got[0]
            self.log(f"(paused: {'; '.join(got[1])})")
        return bool(got and got[0])

    def resume(self):
        """Plays again what he paused (and nothing else)."""
        sessions, self.paused = self.paused, []
        if not sessions:
            return

        async def go():
            for s in sessions:
                try:
                    await s.try_play_async()
                except Exception:  # noqa: BLE001 (that app has closed)
                    pass
        self._run(go())

    def now_playing(self):
        """'Good Mythical Morning... (Microsoft Edge)', or "" if nothing is playing."""
        paused_ids = {s.source_app_user_model_id for s in self.paused}

        async def go():
            out = []
            for s in await self._sessions():
                if not self._playing(s) and s.source_app_user_model_id not in paused_ids:
                    continue
                p = await s.try_get_media_properties_async()
                if p and p.title:
                    who = f" by {p.artist}" if p.artist else ""
                    app = app_name(s.source_app_user_model_id)
                    out.append(f"\"{p.title}\"{who}" + (f" ({app})" if app else ""))
            return "; ".join(out)
        return self._run(go()) or ""


def app_name(aumid):
    """'MSEdge' / 'Spotify.exe' / 'Microsoft.ZuneMusic_8wekyb...!Microsoft.ZuneMusic' -> a readable name."""
    import re
    if re.fullmatch(r"\W*(com-)?[0-9a-f]{6,}\W*", "".join(c for c in (aumid or "") if c.isprintable()), re.I):
        return ""  # an internal code (Edge reports some sites like this): no name to give
    full = (aumid or "").lower()
    for key, name in (("edge", "Microsoft Edge"), ("chrome", "Chrome"), ("firefox", "Firefox"),
                      ("spotify", "Spotify"), ("zunemusic", "Media Player"), ("vlc", "VLC"),
                      ("discord", "Discord")):
        if key in full:
            return name
    # "Publisher.Name_hash!App" -> "Name"; "C:\\...\\thing.exe" -> "thing"
    a = (aumid or "an app").split("!")[0].rsplit("\\", 1)[-1]
    a = a.split("_")[0].rsplit(".", 1)[-1] if "_" in a else a
    a = a.removesuffix(".exe").removesuffix(".EXE")
    if re.search(r"(^|\W)(com-)?[0-9a-f]{6,}($|\W)", a, re.I) and not re.search(r"[g-z]{3}", a.lower()):
        return ""  # still a code
    return a or "an app"
