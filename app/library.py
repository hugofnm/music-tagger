"""Scan du dossier musique et lecture des tags existants."""
import hashlib
import os
import re
from dataclasses import dataclass, field

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")

# Clés "easy" supplémentaires pour lire uniformément tous les formats
for key, frame in (("deezer_album_id", "DEEZER_ALBUM_ID"), ("deezer_track_id", "DEEZER_TRACK_ID")):
    EasyID3.RegisterTXXXKey(key, frame)
    EasyMP4Tags.RegisterFreeformKey(key, frame)


@dataclass
class Track:
    path: str
    ext: str
    duration: float = 0.0
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    tracknumber: int | None = None
    discnumber: int | None = None
    date: str = ""
    genre: str = ""
    has_cover: bool = False
    deezer_album_id: str = ""
    error: str = ""

    def to_dict(self, root: str):
        d = self.__dict__.copy()
        d["rel"] = os.path.relpath(self.path, root)
        d["filename"] = os.path.basename(self.path)
        return d


@dataclass
class Album:
    id: str
    directory: str
    artist: str
    title: str
    tracks: list[Track] = field(default_factory=list)
    folder_cover: str | None = None
    # état du matching
    status: str = "untagged"  # untagged | matched | nomatch | tagged | error
    match: dict | None = None
    message: str = ""

    @property
    def has_cover(self):
        return bool(self.folder_cover) or all(t.has_cover for t in self.tracks)

    def summary(self, root: str):
        m = self.match or {}
        dz = m.get("album") or {}
        return {
            "id": self.id,
            "artist": self.artist,
            "title": self.title,
            "dir": os.path.relpath(self.directory, root),
            "n_tracks": len(self.tracks),
            "status": self.status,
            "message": self.message,
            "has_cover": self.has_cover,
            "confidence": m.get("confidence"),
            "deezer": {
                "id": dz.get("id"),
                "title": dz.get("title"),
                "artist": (dz.get("artist") or {}).get("name"),
                "cover": dz.get("cover_medium"),
                "year": (dz.get("release_date") or "")[:4],
            } if dz else None,
        }


def _first(tags, key):
    try:
        v = tags.get(key)
    except (KeyError, ValueError):
        return ""
    if not v:
        return ""
    return str(v[0]).strip()


def _int(s):
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else None


def _has_embedded_cover(f) -> bool:
    try:
        if getattr(f, "pictures", None):
            return True
        tags = f.tags
        if tags is None:
            return False
        if hasattr(tags, "getall"):  # ID3
            return bool(tags.getall("APIC"))
        if "covr" in tags:  # MP4
            return True
        if "metadata_block_picture" in tags or "METADATA_BLOCK_PICTURE" in tags:
            return True
    except Exception:
        pass
    return False


TRACK_PREFIX = re.compile(r"^\s*(?:(\d{1,2})[-.])?(\d{1,3})\s*[-._)]*\s*")


def read_track(path: str) -> Track:
    ext = os.path.splitext(path)[1].lower()
    t = Track(path=path, ext=ext)
    try:
        raw = mutagen.File(path)
        if raw is None:
            t.error = "format non reconnu"
            return t
        t.duration = float(getattr(raw.info, "length", 0) or 0)
        t.has_cover = _has_embedded_cover(raw)
        easy = mutagen.File(path, easy=True)
        tags = easy.tags if easy is not None and easy.tags is not None else {}
        t.title = _first(tags, "title")
        t.artist = _first(tags, "artist")
        t.album = _first(tags, "album")
        t.albumartist = _first(tags, "albumartist")
        t.tracknumber = _int(_first(tags, "tracknumber"))
        t.discnumber = _int(_first(tags, "discnumber"))
        t.date = _first(tags, "date")
        t.genre = _first(tags, "genre")
        t.deezer_album_id = _first(tags, "deezer_album_id")
    except Exception as e:  # fichier corrompu, etc.
        t.error = str(e)

    # Réparation minimale à partir du nom de fichier si pas de titre / numéro
    stem = os.path.splitext(os.path.basename(path))[0]
    m = TRACK_PREFIX.match(stem)
    if t.tracknumber is None and m and m.group(2):
        t.tracknumber = int(m.group(2))
        if m.group(1) and t.discnumber is None:
            t.discnumber = int(m.group(1))
    if not t.title:
        title = TRACK_PREFIX.sub("", stem) if m else stem
        # "Artiste - Titre" -> "Titre"
        if " - " in title:
            title = title.split(" - ", 1)[1]
        t.title = title.strip() or stem
    return t


def _guess_from_dir(directory: str):
    """Déduit (artiste, album) du chemin : 'Artiste/Album' ou 'Artiste - Album'."""
    name = os.path.basename(directory.rstrip("/"))
    name = re.sub(r"\s*[\[(](?:19|20)\d\d[\])]\s*", " ", name).strip()  # retire (2004)
    if " - " in name:
        a, b = name.split(" - ", 1)
        return a.strip(), b.strip()
    parent = os.path.basename(os.path.dirname(directory.rstrip("/")))
    return parent, name


def scan(root: str, progress=None) -> list[Album]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in AUDIO_EXT:
                files.append(os.path.join(dirpath, fn))

    groups: dict[tuple, list[Track]] = {}
    for i, path in enumerate(files):
        if progress:
            progress(i, len(files), os.path.relpath(path, root))
        t = read_track(path)
        directory = os.path.dirname(path)
        guess_artist, guess_album = _guess_from_dir(directory)
        album = t.album or guess_album
        artist = t.albumartist or t.artist or guess_artist
        # Un dossier = un album, sauf si les tags indiquent plusieurs albums dans le même dossier
        key = (directory, album.casefold())
        groups.setdefault(key, []).append(t)
        if not t.album:
            t.album = album
        if not t.artist:
            t.artist = artist

    albums = []
    for (directory, _), tracks in groups.items():
        # artiste d'album = valeur la plus fréquente (gère les featurings)
        counts: dict[str, int] = {}
        for t in tracks:
            a = t.albumartist or t.artist
            counts[a] = counts.get(a, 0) + 1
        artist = max(counts, key=counts.get) if counts else ""
        if len(counts) > 1 and max(counts.values()) < len(tracks) / 2 and not any(t.albumartist for t in tracks):
            artist = "Various Artists"
        tracks.sort(key=lambda t: (t.discnumber or 1, t.tracknumber or 9999, t.path))
        aid = hashlib.sha1(f"{directory}\0{tracks[0].album}".encode()).hexdigest()[:12]
        al = Album(id=aid, directory=directory, artist=artist, title=tracks[0].album, tracks=tracks)
        for name in COVER_NAMES:
            p = os.path.join(directory, name)
            if os.path.isfile(p):
                al.folder_cover = p
                break
        if all(t.deezer_album_id for t in tracks):
            al.status = "tagged"
        if any(t.error for t in tracks):
            al.message = "; ".join(sorted({t.error for t in tracks if t.error}))[:200]
        albums.append(al)
    albums.sort(key=lambda a: (a.artist.casefold(), a.title.casefold()))
    return albums
