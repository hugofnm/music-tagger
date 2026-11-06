"""Écriture des tags (FLAC, MP3, M4A, OGG/Opus), pochette, sauvegarde et restauration."""
import base64
import datetime as dt
import json
import os
import re

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC, ID3NoHeaderError, TALB, TBPM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TPUB, TRCK, TSRC, TXXX,
)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from .deezer import Deezer
from .library import Album, Track

RELEASE_TYPES = {"album": "album", "ep": "ep", "single": "single", "compile": "compilation"}


# --------------------------------------------------------------------------- meta
def _artist_display(detail: dict, fallback: str):
    contributors = detail.get("contributors") or []
    main = [c["name"] for c in contributors if c.get("role") == "Main"]
    feat = [c["name"] for c in contributors if c.get("role") == "Featured"]
    if not main:
        main = [(detail.get("artist") or {}).get("name") or fallback]
    names = main + [f for f in feat if f not in main]
    display = ", ".join(main)
    if feat:
        display += " feat. " + ", ".join(feat)
    return display, names


def build_meta(local: Track, dz_album: dict, remote: dict | None, detail: dict | None,
               disc_totals: dict, disctotal: int, mode: str) -> dict:
    genres = [g["name"] for g in (dz_album.get("genres") or {}).get("data", []) if g.get("name")]
    meta = {
        "album": dz_album.get("title"),
        "albumartist": (dz_album.get("artist") or {}).get("name"),
        "date": dz_album.get("release_date") or "",
        "genre": genres,
        "label": dz_album.get("label") or "",
        "barcode": dz_album.get("upc") or "",
        "releasetype": RELEASE_TYPES.get(dz_album.get("record_type") or "", ""),
        "deezer_album_id": str(dz_album.get("id") or ""),
    }
    if remote:
        detail = detail or {}
        disc = remote.get("disk_number") or 1
        ra = remote.get("artist")
        display, names = _artist_display(detail, (ra.get("name") if isinstance(ra, dict) else ra) or "")
        meta.update({
            "title": remote.get("title") or local.title,
            "artist": display,
            "artists": names,
            "tracknumber": remote.get("track_position"),
            "tracktotal": disc_totals.get(disc),
            "discnumber": disc,
            "disctotal": disctotal,
            "isrc": detail.get("isrc") or remote.get("isrc") or "",
            "bpm": int(round(detail["bpm"])) if detail.get("bpm") else None,
            "deezer_track_id": str(remote.get("id")),
        })
    else:
        # fichier non rapproché : on garde ses infos de piste, on complète l'album
        meta.update({
            "title": local.title,
            "artist": local.artist or meta["albumartist"],
            "tracknumber": local.tracknumber,
            "discnumber": local.discnumber,
        })

    if mode == "fill":  # ne remplace pas ce qui existe déjà
        for key in ("title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date"):
            v = getattr(local, key, None)
            if v:
                meta[key] = v
        if local.genre:
            meta["genre"] = [local.genre]
        if local.artist:
            meta.pop("artists", None)
    return {k: v for k, v in meta.items() if v not in (None, "", [])}


# --------------------------------------------------------------------------- writers
def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _picture(data: bytes) -> Picture:
    p = Picture()
    p.type = 3
    p.mime = _mime(data)
    p.desc = "Cover"
    p.data = data
    return p


VORBIS_KEYS = {
    "title": ["TITLE"], "artist": ["ARTIST"], "artists": ["ARTISTS"], "album": ["ALBUM"],
    "albumartist": ["ALBUMARTIST"], "tracknumber": ["TRACKNUMBER"],
    "tracktotal": ["TRACKTOTAL", "TOTALTRACKS"], "discnumber": ["DISCNUMBER"],
    "disctotal": ["DISCTOTAL", "TOTALDISCS"], "date": ["DATE"], "genre": ["GENRE"],
    "label": ["LABEL"], "isrc": ["ISRC"], "barcode": ["BARCODE"], "releasetype": ["RELEASETYPE"],
    "bpm": ["BPM"], "deezer_album_id": ["DEEZER_ALBUM_ID"], "deezer_track_id": ["DEEZER_TRACK_ID"],
}


def _as_list(v):
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def write_vorbis(path: str, meta: dict, cover: bytes | None):
    f = mutagen.File(path)
    if f.tags is None:
        f.add_tags()
    for key, names in VORBIS_KEYS.items():
        if key in meta:
            for n in names:
                f.tags[n] = _as_list(meta[key])
    if cover:
        pic = _picture(cover)
        if isinstance(f, FLAC):
            f.clear_pictures()
            f.add_picture(pic)
        else:
            for k in ("COVERART", "COVERARTMIME"):
                if k in f.tags:
                    del f.tags[k]
            f.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    f.save()


def _txxx(tags, desc, value):
    tags.delall(f"TXXX:{desc}")
    tags.add(TXXX(encoding=3, desc=desc, text=_as_list(value)))


def write_id3(path: str, meta: dict, cover: bytes | None):
    f = MP3(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    simple = {"title": TIT2, "artist": TPE1, "album": TALB, "albumartist": TPE2,
              "date": TDRC, "genre": TCON, "label": TPUB, "isrc": TSRC, "bpm": TBPM}
    for key, frame in simple.items():
        if key in meta:
            t.setall(frame.__name__, [frame(encoding=3, text=_as_list(meta[key]))])
    if "tracknumber" in meta:
        v = f"{meta['tracknumber']}/{meta['tracktotal']}" if meta.get("tracktotal") else str(meta["tracknumber"])
        t.setall("TRCK", [TRCK(encoding=3, text=[v])])
    if "discnumber" in meta:
        v = f"{meta['discnumber']}/{meta['disctotal']}" if meta.get("disctotal") else str(meta["discnumber"])
        t.setall("TPOS", [TPOS(encoding=3, text=[v])])
    for key, desc in (("artists", "ARTISTS"), ("barcode", "BARCODE"), ("releasetype", "RELEASETYPE"),
                      ("deezer_album_id", "DEEZER_ALBUM_ID"), ("deezer_track_id", "DEEZER_TRACK_ID")):
        if key in meta:
            _txxx(t, desc, meta[key])
    if cover:
        t.delall("APIC")
        t.add(APIC(encoding=3, mime=_mime(cover), type=3, desc="Cover", data=cover))
    f.save(v2_version=4)


def _ff(v):
    return [MP4FreeForm(str(x).encode("utf-8")) for x in _as_list(v)]


def write_mp4(path: str, meta: dict, cover: bytes | None):
    f = MP4(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    for key, atom in (("title", "\xa9nam"), ("artist", "\xa9ART"), ("album", "\xa9alb"),
                      ("albumartist", "aART"), ("date", "\xa9day"), ("genre", "\xa9gen")):
        if key in meta:
            t[atom] = _as_list(meta[key])
    if "tracknumber" in meta:
        t["trkn"] = [(int(meta["tracknumber"]), int(meta.get("tracktotal") or 0))]
    if "discnumber" in meta:
        t["disk"] = [(int(meta["discnumber"]), int(meta.get("disctotal") or 0))]
    if "bpm" in meta:
        t["tmpo"] = [int(meta["bpm"])]
    for key, name in (("artists", "ARTISTS"), ("label", "LABEL"), ("isrc", "ISRC"), ("barcode", "BARCODE"),
                      ("releasetype", "RELEASETYPE"), ("deezer_album_id", "DEEZER_ALBUM_ID"),
                      ("deezer_track_id", "DEEZER_TRACK_ID")):
        if key in meta:
            t[f"----:com.apple.iTunes:{name}"] = _ff(meta[key])
    if cover:
        fmt = MP4Cover.FORMAT_PNG if _mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        t["covr"] = [MP4Cover(cover, imageformat=fmt)]
    f.save()


def write_tags(path: str, meta: dict, cover: bytes | None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        write_id3(path, meta, cover)
    elif ext in (".m4a", ".mp4", ".aac"):
        write_mp4(path, meta, cover)
    elif ext in (".flac", ".ogg", ".oga", ".opus"):
        write_vorbis(path, meta, cover)
    else:
        raise ValueError(f"Format non pris en charge : {ext}")


# --------------------------------------------------------------------------- backup
def _snapshot(path: str) -> dict:
    try:
        easy = mutagen.File(path, easy=True)
        tags = {k: [str(x) for x in v] for k, v in (easy.tags or {}).items()} if easy else {}
    except ID3NoHeaderError:
        tags = {}
    from .library import _has_embedded_cover
    return {"tags": tags, "had_cover": _has_embedded_cover(mutagen.File(path))}


def restore_backup(backup_file: str) -> list[str]:
    with open(backup_file, encoding="utf-8") as fh:
        data = json.load(fh)
    restored = []
    for entry in data["files"]:
        path = entry.get("new_path") or entry["path"]
        if not os.path.exists(path):
            continue
        f = mutagen.File(path)
        if isinstance(f, FLAC):
            if f.tags is not None:
                f.tags.clear()
            if not entry["had_cover"]:
                f.clear_pictures()
            f.save()
        else:
            f.delete()
        easy = mutagen.File(path, easy=True)
        if easy.tags is None:
            easy.add_tags()
        for k, v in entry["tags"].items():
            try:
                easy.tags[k] = v
            except (KeyError, ValueError):
                pass
        easy.save()
        if entry.get("new_path") and entry["new_path"] != entry["path"] and not os.path.exists(entry["path"]):
            os.rename(entry["new_path"], entry["path"])
            path = entry["path"]
        restored.append(path)
    for p in data.get("created", []):
        if os.path.exists(p):
            os.remove(p)
    return restored


# --------------------------------------------------------------------------- apply
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s: str) -> str:
    s = BAD_CHARS.sub("_", s).strip().rstrip(".")
    return s[:180] or "_"


def apply_album(album: Album, match: dict, dz: Deezer, options: dict, data_dir: str) -> dict:
    mode = options.get("mode", "overwrite")
    dz_album = match["album"]
    remote_by_id = {t["id"]: t for t in match["tracks"]}
    disc_totals: dict[int, int] = {}
    for t in match["tracks"]:
        d = t.get("disk_number") or 1
        disc_totals[d] = disc_totals.get(d, 0) + 1
    disctotal = max(disc_totals) if disc_totals else 1

    cover = None
    if options.get("embed_cover", True) or options.get("cover_file", True):
        cover = dz.cover(dz_album, int(options.get("cover_size", 1200)))

    backup = {"album_id": album.id, "directory": album.directory, "date": dt.datetime.now().isoformat(),
              "deezer_album_id": dz_album.get("id"), "files": [], "created": []}
    warnings = []
    written = 0
    for i, local in enumerate(album.tracks):
        m = match["mapping"][i] if i < len(match["mapping"]) else None
        remote = remote_by_id.get(m["track_id"]) if m else None
        if not remote and not options.get("tag_unmatched", True):
            continue
        detail = dz.track(remote["id"]) if remote else None
        meta = build_meta(local, dz_album, remote, detail, disc_totals, disctotal, mode)
        entry = {"path": local.path, **_snapshot(local.path)}
        backup["files"].append(entry)
        try:
            write_tags(local.path, meta, cover if options.get("embed_cover", True) else None)
            written += 1
        except Exception as e:
            warnings.append(f"{os.path.basename(local.path)} : {e}")
            continue
        if options.get("rename") and meta.get("tracknumber") and meta.get("title"):
            n = int(meta["tracknumber"])
            prefix = f"{meta.get('discnumber', 1)}-{n:02d}" if disctotal > 1 else f"{n:02d}"
            new = os.path.join(os.path.dirname(local.path), safe_name(f"{prefix} - {meta['title']}") + local.ext)
            if new != local.path:
                if os.path.exists(new):
                    warnings.append(f"Renommage ignoré, {os.path.basename(new)} existe déjà")
                else:
                    os.rename(local.path, new)
                    entry["new_path"] = new
                    local.path = new

    if cover and options.get("cover_file", True):
        target = os.path.join(album.directory, "cover.jpg" if _mime(cover) == "image/jpeg" else "cover.png")
        if not album.folder_cover:
            try:
                with open(target, "wb") as fh:
                    fh.write(cover)
                backup["created"].append(target)
                album.folder_cover = target
            except OSError as e:
                warnings.append(f"cover : {e}")

    os.makedirs(os.path.join(data_dir, "backups"), exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bfile = os.path.join(data_dir, "backups", f"{album.id}-{stamp}.json")
    with open(bfile, "w", encoding="utf-8") as fh:
        json.dump(backup, fh, ensure_ascii=False, indent=1)
    return {"written": written, "warnings": warnings, "backup": bfile, "cover": bool(cover)}
