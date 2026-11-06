"""Rapprochement album local <-> album Deezer, puis piste par piste."""
import re
import unicodedata

from rapidfuzz import fuzz

from .deezer import Deezer
from .library import Album

BRACKETS = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
NOISE = re.compile(
    r"\b(remaster(ed)?|deluxe|edition|version|bonus|expanded|anniversary|mono|stereo|explicit)\b"
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace("&", " and ").replace("’", "'")
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sim(a: str, b: str) -> float:
    """Similarité 0..1 tolérante aux '(Remastered)', '- Live', ordre des mots…"""
    if not a or not b:
        return 0.0
    na, nb = norm(a), norm(b)
    best = max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb))
    sa = norm(BRACKETS.sub("", a.split(" - ")[0]))
    sb = norm(BRACKETS.sub("", b.split(" - ")[0]))
    if sa and sb:
        best = max(best, fuzz.ratio(sa, sb) - 3)
    sa2, sb2 = NOISE.sub("", na).strip(), NOISE.sub("", nb).strip()
    if sa2 and sb2:
        best = max(best, fuzz.token_set_ratio(sa2, sb2) - 5)
    return max(0.0, min(1.0, best / 100))


def duration_score(local: float, remote: float) -> float:
    if not local or not remote:
        return 0.5
    diff = abs(local - remote)
    if diff <= 2:
        return 1.0
    return max(0.0, 1 - (diff - 2) / 15)


def album_score(local: Album, cand: dict) -> float:
    title = sim(local.title, cand.get("title", ""))
    artist = sim(local.artist, (cand.get("artist") or {}).get("name", ""))
    if local.artist.casefold() in ("various artists", "va", "compilation"):
        artist = 0.8
    n_remote = cand.get("nb_tracks") or 0
    n_local = len(local.tracks)
    count = 1.0 if n_remote == n_local else max(0.0, 1 - abs(n_remote - n_local) / max(n_local, 1))
    return 0.45 * title + 0.35 * artist + 0.20 * count


def map_tracks(local: Album, remote_tracks: list[dict]):
    """Affectation gloutonne fichier -> piste Deezer (titre + durée + n°)."""
    pairs = []
    for i, t in enumerate(local.tracks):
        for j, r in enumerate(remote_tracks):
            ts = max(sim(t.title, r.get("title", "")), sim(t.title, r.get("title_short", "")))
            ds = duration_score(t.duration, r.get("duration") or 0)
            pos = 0.0
            if t.tracknumber and t.tracknumber == r.get("track_position") and (
                (t.discnumber or 1) == (r.get("disk_number") or 1)
            ):
                pos = 1.0
            score = 0.6 * ts + 0.25 * ds + 0.15 * pos
            # titre absent / inutilisable : se fier au n° et à la durée
            if not t.title or ts < 0.3:
                score = max(score, 0.55 * pos + 0.45 * ds) if pos else score
            pairs.append((score, i, j))
    pairs.sort(reverse=True)
    used_i, used_j = set(), set()
    mapping = [None] * len(local.tracks)
    for score, i, j in pairs:
        if score < 0.5 or i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        mapping[i] = {"track_id": remote_tracks[j]["id"], "score": round(score, 3)}
    return mapping


def evaluate(local: Album, dz: Deezer, deezer_album_id) -> dict:
    album = dz.album(deezer_album_id)
    tracks = album["tracks_full"]
    mapping = map_tracks(local, tracks)
    matched = [m for m in mapping if m]
    track_avg = sum(m["score"] for m in matched) / len(local.tracks) if local.tracks else 0
    a_score = album_score(local, album)
    confidence = round(0.4 * a_score + 0.6 * track_avg, 3)
    album_light = {k: v for k, v in album.items() if k not in ("tracks", "tracks_full")}
    return {
        "album": album_light,
        "tracks": [
            {
                "id": t["id"],
                "title": t.get("title"),
                "artist": (t.get("artist") or {}).get("name"),
                "duration": t.get("duration"),
                "track_position": t.get("track_position"),
                "disk_number": t.get("disk_number"),
                "isrc": t.get("isrc"),
            }
            for t in tracks
        ],
        "mapping": mapping,
        "confidence": confidence,
        "album_score": round(a_score, 3),
    }


def auto_match(local: Album, dz: Deezer) -> dict | None:
    cands = dz.search_albums(local.artist, local.title)
    if not cands:
        return None
    ranked = sorted(cands, key=lambda c: album_score(local, c), reverse=True)[:3]
    best = None
    for c in ranked:
        res = evaluate(local, dz, c["id"])
        if best is None or res["confidence"] > best["confidence"]:
            best = res
        if res["confidence"] >= 0.95:
            break
    return best
