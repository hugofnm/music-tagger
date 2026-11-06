"""Client minimal pour l'API publique Deezer (aucune clé requise).

Limite officielle : 50 requêtes / 5 secondes. On espace les appels et on
réessaie automatiquement en cas de dépassement de quota.
"""
import threading
import time

import requests

API = "https://api.deezer.com"


class DeezerError(Exception):
    pass


class Deezer:
    def __init__(self, min_interval: float = 0.12):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "music-tagger/1.0"
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._cache: dict = {}

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict | None = None):
        key = (path, tuple(sorted((params or {}).items())))
        if key in self._cache:
            return self._cache[key]
        for attempt in range(5):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                r = self.session.get(API + path, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                if attempt == 4:
                    raise DeezerError(f"Deezer injoignable : {e}") from e
                time.sleep(1 + attempt)
                continue
            if isinstance(data, dict) and "error" in data:
                err = data["error"] or {}
                if err.get("code") == 4:  # Quota limit exceeded
                    time.sleep(2 * (attempt + 1))
                    continue
                if err.get("code") == 800:  # no data
                    data = {}
                else:
                    raise DeezerError(err.get("message") or str(err))
            self._cache[key] = data
            return data
        raise DeezerError("Quota Deezer dépassé, réessaie dans quelques secondes")

    def get_bytes(self, url: str) -> bytes:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    # ------------------------------------------------------------------ api
    def search_albums(self, artist: str = "", album: str = "", free: str = "", limit: int = 12):
        results = []
        queries = []
        if free:
            queries.append(free)
        else:
            if artist and album:
                queries.append(f'artist:"{artist}" album:"{album}"')
                queries.append(f"{artist} {album}")
            queries.append(album or artist)
        seen = set()
        for q in queries:
            if not q:
                continue
            for a in self._get("/search/album", {"q": q, "limit": limit}).get("data", []):
                if a["id"] not in seen:
                    seen.add(a["id"])
                    results.append(a)
            if len(results) >= 3:
                break
        return results

    def album(self, album_id: int | str) -> dict:
        a = self._get(f"/album/{album_id}")
        if not a:
            raise DeezerError(f"Album Deezer {album_id} introuvable")
        tracks = self._get(f"/album/{album_id}/tracks", {"limit": 500}).get("data", [])
        if not tracks:
            tracks = (a.get("tracks") or {}).get("data", [])
        for i, t in enumerate(tracks):
            t.setdefault("track_position", i + 1)
            t.setdefault("disk_number", 1)
        a["tracks_full"] = tracks
        return a

    def track(self, track_id: int | str) -> dict:
        return self._get(f"/track/{track_id}")

    def cover(self, album: dict, size: int = 1200) -> bytes | None:
        """Télécharge la pochette ; tente une taille supérieure à cover_xl (1000px)."""
        xl = album.get("cover_xl")
        if not xl:
            return None
        if size and size != 1000 and "1000x1000" in xl:
            try:
                data = self.get_bytes(xl.replace("1000x1000", f"{size}x{size}"))
                if data[:3] == b"\xff\xd8\xff":
                    return data
            except requests.RequestException:
                pass
        try:
            return self.get_bytes(xl)
        except requests.RequestException:
            return None
