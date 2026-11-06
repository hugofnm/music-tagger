"""Music Tagger — API + interface web."""
import glob
import os
import re
import threading
import traceback

import mutagen
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import library, matcher, tagger
from .deezer import Deezer, DeezerError

MUSIC_DIR = os.path.abspath(os.environ.get("MUSIC_DIR", "/music"))
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", "/data"))
COVER_SIZE = int(os.environ.get("COVER_SIZE", "1200"))
AUTO_SCAN = os.environ.get("AUTO_SCAN", "1") not in ("0", "false", "no")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Music Tagger")
dz = Deezer()


class State:
    def __init__(self):
        self.albums: dict[str, library.Album] = {}
        self.lock = threading.RLock()
        self.job = {"name": None, "running": False, "done": 0, "total": 0, "current": "", "errors": []}


S = State()


# --------------------------------------------------------------------------- jobs
def run_job(name: str, fn):
    with S.lock:
        if S.job["running"]:
            raise HTTPException(409, f"Une tâche est déjà en cours : {S.job['name']}")
        S.job = {"name": name, "running": True, "done": 0, "total": 0, "current": "", "errors": []}

    def wrapper():
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            S.job["errors"].append(str(e))
        finally:
            S.job["running"] = False
            S.job["current"] = ""

    threading.Thread(target=wrapper, daemon=True).start()


def progress(i, total, current):
    S.job.update(done=i, total=total, current=current)


def do_scan():
    if not os.path.isdir(MUSIC_DIR):
        raise RuntimeError(f"Dossier musique introuvable : {MUSIC_DIR}")
    albums = library.scan(MUSIC_DIR, progress)
    with S.lock:
        old = S.albums
        S.albums = {}
        for a in albums:
            prev = old.get(a.id)
            if prev and prev.match and a.status != "tagged":  # conserve un matching déjà fait
                a.match, a.status = prev.match, prev.status
            S.albums[a.id] = a
    S.job.update(done=len(albums), total=len(albums))


def match_one(a: library.Album, deezer_id=None):
    try:
        res = matcher.evaluate(a, dz, deezer_id) if deezer_id else matcher.auto_match(a, dz)
    except DeezerError as e:
        a.status, a.message = "error", str(e)
        return
    if not res:
        a.match, a.status, a.message = None, "nomatch", "Aucun résultat Deezer"
        return
    a.match = res
    a.status = "matched"
    unmatched = sum(1 for m in res["mapping"] if not m)
    a.message = f"{unmatched} fichier(s) non rapproché(s)" if unmatched else ""


def apply_one(a: library.Album, options: dict):
    options = {"cover_size": COVER_SIZE, **options}
    res = tagger.apply_album(a, a.match, dz, options, DATA_DIR)
    # relit les fichiers pour refléter l'état réel
    a.tracks = [library.read_track(t.path) for t in a.tracks]
    first = a.tracks[0] if a.tracks else None
    if first and first.album:
        a.title = first.album
        a.artist = first.albumartist or first.artist or a.artist
    a.status = "tagged" if res["written"] else "error"
    a.message = "; ".join(res["warnings"])[:300]
    return res


@app.on_event("startup")
def startup():
    os.makedirs(DATA_DIR, exist_ok=True)
    if AUTO_SCAN:
        run_job("scan", do_scan)


# --------------------------------------------------------------------------- helpers
def get_album(aid: str) -> library.Album:
    a = S.albums.get(aid)
    if not a:
        raise HTTPException(404, "Album inconnu (relance un scan ?)")
    return a


def album_detail(a: library.Album):
    d = a.summary(MUSIC_DIR)
    d["tracks"] = [t.to_dict(MUSIC_DIR) for t in a.tracks]
    d["match"] = a.match
    d["backups"] = sorted(os.path.basename(p) for p in glob.glob(os.path.join(DATA_DIR, "backups", f"{a.id}-*.json")))
    return d


def parse_deezer_id(value) -> int:
    m = re.search(r"album/(\d+)", str(value)) or re.fullmatch(r"\s*(\d+)\s*", str(value))
    if not m:
        raise HTTPException(400, "Donne un ID ou une URL d'album Deezer (deezer.com/album/123…)")
    return int(m.group(1))


# --------------------------------------------------------------------------- routes
class MatchBody(BaseModel):
    deezer_id: str | None = None


class MappingBody(BaseModel):
    index: int
    track_id: int | None = None


class ApplyOptions(BaseModel):
    mode: str = "overwrite"
    embed_cover: bool = True
    cover_file: bool = True
    rename: bool = False
    tag_unmatched: bool = True


class BatchApply(ApplyOptions):
    min_confidence: float = 0.85


@app.get("/api/status")
def status():
    counts: dict[str, int] = {}
    for a in S.albums.values():
        counts[a.status] = counts.get(a.status, 0) + 1
    return {"job": S.job, "music_dir": MUSIC_DIR, "counts": counts, "total": len(S.albums)}


@app.get("/api/albums")
def albums():
    return [a.summary(MUSIC_DIR) for a in S.albums.values()]


@app.post("/api/scan")
def scan():
    run_job("scan", do_scan)
    return {"ok": True}


@app.get("/api/albums/{aid}")
def album(aid: str):
    return album_detail(get_album(aid))


@app.post("/api/albums/{aid}/match")
def match(aid: str, body: MatchBody | None = None):
    a = get_album(aid)
    if S.job["running"] and S.job["name"] != "scan":
        raise HTTPException(409, "Attends la fin de la tâche en cours")
    deezer_id = parse_deezer_id(body.deezer_id) if body and body.deezer_id else None
    match_one(a, deezer_id)
    if a.status == "error":
        raise HTTPException(502, a.message)
    return album_detail(a)


@app.get("/api/albums/{aid}/search")
def search(aid: str, q: str = ""):
    a = get_album(aid)
    try:
        res = dz.search_albums(a.artist, a.title, free=q.strip())
    except DeezerError as e:
        raise HTTPException(502, str(e))
    out = []
    for c in res:
        out.append({
            "id": c["id"], "title": c.get("title"), "artist": (c.get("artist") or {}).get("name"),
            "cover": c.get("cover_medium"), "nb_tracks": c.get("nb_tracks"),
            "record_type": c.get("record_type"), "score": round(matcher.album_score(a, c), 3),
            "link": c.get("link"),
        })
    return sorted(out, key=lambda c: c["score"], reverse=True)


@app.post("/api/albums/{aid}/mapping")
def set_mapping(aid: str, body: MappingBody):
    a = get_album(aid)
    if not a.match:
        raise HTTPException(400, "Pas de correspondance Deezer pour cet album")
    mapping = a.match["mapping"]
    if not 0 <= body.index < len(mapping):
        raise HTTPException(400, "Index invalide")
    if body.track_id is not None:
        for i, m in enumerate(mapping):  # une piste Deezer ne sert qu'une fois
            if m and m["track_id"] == body.track_id:
                mapping[i] = None
        mapping[body.index] = {"track_id": body.track_id, "score": 1.0, "manual": True}
    else:
        mapping[body.index] = None
    return album_detail(a)


@app.post("/api/albums/{aid}/apply")
def apply(aid: str, opts: ApplyOptions):
    a = get_album(aid)
    if not a.match:
        raise HTTPException(400, "Choisis d'abord un album Deezer")
    if S.job["running"] and S.job["name"] != "scan":
        raise HTTPException(409, "Attends la fin de la tâche en cours")
    try:
        res = apply_one(a, opts.model_dump())
    except (DeezerError, OSError, mutagen.MutagenError) as e:
        a.status, a.message = "error", str(e)
        raise HTTPException(500, str(e))
    return {"result": res, "album": album_detail(a)}


@app.post("/api/albums/{aid}/restore")
def restore(aid: str):
    a = get_album(aid)
    files = sorted(glob.glob(os.path.join(DATA_DIR, "backups", f"{a.id}-*.json")))
    if not files:
        raise HTTPException(404, "Aucune sauvegarde pour cet album")
    restored = tagger.restore_backup(files[-1])
    os.rename(files[-1], files[-1] + ".restored")
    paths = restored + [t.path for t in a.tracks if t.path not in restored and os.path.exists(t.path)]
    a.tracks = sorted((library.read_track(p) for p in paths),
                      key=lambda t: (t.discnumber or 1, t.tracknumber or 9999, t.path))
    a.match, a.status = None, "untagged"
    a.message = f"{len(restored)} fichier(s) restauré(s)"
    if a.folder_cover and not os.path.exists(a.folder_cover):
        a.folder_cover = None
    return album_detail(a)


@app.get("/api/albums/{aid}/cover")
def local_cover(aid: str):
    a = get_album(aid)
    if a.folder_cover and os.path.exists(a.folder_cover):
        return FileResponse(a.folder_cover)
    for t in a.tracks:
        try:
            f = mutagen.File(t.path)
            pics = getattr(f, "pictures", None)
            if pics:
                return Response(pics[0].data, media_type=pics[0].mime)
            tags = f.tags
            if tags is not None and hasattr(tags, "getall") and tags.getall("APIC"):
                p = tags.getall("APIC")[0]
                return Response(p.data, media_type=p.mime)
            if tags is not None and "covr" in tags:
                return Response(bytes(tags["covr"][0]), media_type="image/jpeg")
        except Exception:
            continue
    raise HTTPException(404)


@app.post("/api/batch/match")
def batch_match(include_tagged: bool = False):
    targets = [a for a in S.albums.values()
               if a.status in ("untagged", "nomatch", "error") or (include_tagged and a.status == "tagged")]

    def work():
        for i, a in enumerate(targets):
            progress(i, len(targets), f"{a.artist} — {a.title}")
            match_one(a)
        progress(len(targets), len(targets), "")

    run_job("match", work)
    return {"queued": len(targets)}


@app.post("/api/batch/apply")
def batch_apply(opts: BatchApply):
    options = opts.model_dump()
    threshold = options.pop("min_confidence")
    targets = [a for a in S.albums.values()
               if a.status == "matched" and a.match and a.match["confidence"] >= threshold]

    def work():
        for i, a in enumerate(targets):
            progress(i, len(targets), f"{a.artist} — {a.title}")
            try:
                apply_one(a, options)
            except Exception as e:
                a.status, a.message = "error", str(e)
                S.job["errors"].append(f"{a.artist} — {a.title} : {e}")
        progress(len(targets), len(targets), "")

    run_job("apply", work)
    return {"queued": len(targets)}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
