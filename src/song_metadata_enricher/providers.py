from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps

from .cache import Cache
from .jobs import JobContext
from .lyrics import normalize_text
from .model import Artwork, Candidate, Metadata, Track

USER_AGENT = "Tracksmith/0.1 (local desktop application; https://musicbrainz.org/doc/MusicBrainz_API)"


class MusicIdentificationService(Protocol):
    def identify(self, track: Track, context: JobContext) -> list[Candidate]: ...


class MetadataProvider(Protocol):
    def search(self, artist: str, title: str, context: JobContext) -> list[Candidate]: ...


class ArtworkProvider(Protocol):
    def fetch(self, release_id: str, context: JobContext) -> Artwork: ...


@dataclass
class LyricsCandidate:
    title: str
    artist: str
    album: str
    lyrics: str


class LyricsProvider(Protocol):
    def search(self, artist: str, title: str, context: JobContext) -> list[LyricsCandidate]: ...


def parse_filename(filename: str) -> tuple[str, str]:
    text = Path(filename).stem.replace("_", " ")
    text = re.sub(r"^\s*\d{1,3}[. -]+", "", text)
    text = re.sub(r"\s*[\[(](?:edited|edit|trimmed|cut|custom)[^\])]*[\])]", "", text, flags=re.I)
    text = re.sub(r"\s+(?:edited|trimmed|custom|cut)\s*$", "", text, flags=re.I).strip()
    parts = re.split(r"\s+[–—-]\s+", text, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", text)


class HttpClient:
    _lock = threading.Lock()
    _last_musicbrainz = 0.0

    def __init__(self, cache: Cache):
        self.cache = cache

    def json(self, url: str, context: JobContext, cache: bool = True):
        identity = {"url": url}
        stored = self.cache.get("http", identity, 7 * 86400) if cache else None
        if stored is not None:
            return stored
        if urllib.parse.urlparse(url).hostname == "musicbrainz.org":
            with self._lock:
                while time.monotonic() - type(self)._last_musicbrainz < 1.1:
                    context.check()
                    context.cancelled.wait(0.1)
                type(self)._last_musicbrainz = time.monotonic()
                result = json.loads(self.bytes(url, context))
        else:
            result = json.loads(self.bytes(url, context))
        if cache:
            self.cache.put("http", identity, result)
        return result

    def bytes(
        self,
        url: str,
        context: JobContext,
        limit: int = 15 * 1024 * 1024,
        body: bytes | None = None,
    ) -> bytes:
        context.check()
        request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = bytearray()
                while chunk := response.read(65536):
                    context.check()
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("Provider response exceeds size limit")
            context.check()
            return bytes(data)
        except urllib.error.HTTPError as exc:
            # Do not include URLs: AcoustID requests can contain credentials.
            raise RuntimeError(f"Provider returned HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("Provider could not be reached; check network connection") from None


def _artist(credits) -> str:
    return "".join(
        credit.get("name", credit.get("artist", {}).get("name", "")) + credit.get("joinphrase", "")
        for credit in credits
        if isinstance(credit, dict)
    )


def merge_release_candidates(*groups: list[Candidate]) -> list[Candidate]:
    """Retain distinct recording/edition choices without duplicate rows."""
    result = {}
    for group in groups:
        for candidate in group:
            key = (
                candidate.release_id,
                candidate.recording_id,
                candidate.metadata.artist,
                candidate.metadata.title,
                candidate.metadata.album,
            )
            if key not in result or candidate.confidence > result[key].confidence:
                result[key] = candidate
    return list(result.values())


class MusicBrainzProvider:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(
        self, artist: str, title: str, context: JobContext, *, prefer_albums: bool = False
    ) -> list[Candidate]:
        if not title.strip():
            raise ValueError("Enter a title or use the filename suggestion first")
        title_terms = re.findall(r"[^\W_]+", normalize_text(title))
        artist_terms = re.findall(r"[^\W_]+", normalize_text(artist))
        if not title_terms:
            raise ValueError("Title must contain searchable words")
        # Try normal analyzed terms first. Fuzzy indexes can miss even close spelling;
        # a bounded prefix fallback retrieves candidates for local fuzzy ranking.
        data = {}
        for mode in ("exact", "fuzzy", "prefix"):

            def term_query(term):
                if mode == "fuzzy" and len(term) >= 4:
                    return term + "~"
                if mode == "prefix" and len(term) >= 4:
                    return term[: max(2, len(term) // 2)] + "*"
                return term

            query = "recording:(" + " AND ".join(map(term_query, title_terms)) + ")"
            if artist_terms:
                query += " AND artist:(" + " AND ".join(map(term_query, artist_terms)) + ")"
            if prefer_albums:
                unwanted = [
                    version
                    for version in ("live", "remix", "karaoke", "demo")
                    if version not in title.lower()
                ]
                if unwanted:
                    query += " AND NOT comment:(" + " OR ".join(unwanted) + ")"
            url = "https://musicbrainz.org/ws/2/recording/?" + urllib.parse.urlencode(
                {"query": query, "fmt": "json", "limit": 100 if prefer_albums else 20}
            )
            data = self.http.json(url, context)
            if data.get("recordings"):
                break
        results = []
        ranks = {}
        for item in data.get("recordings", []):
            found_artist = _artist(item.get("artist-credit", []))
            similarity = SequenceMatcher(
                None, normalize_text(title), normalize_text(item["title"])
            ).ratio()
            if similarity < 0.72:
                continue
            if artist:
                artist_similarity = SequenceMatcher(
                    None, normalize_text(artist), normalize_text(found_artist)
                ).ratio()
                if artist_similarity < 0.75:
                    continue
                similarity = (similarity + artist_similarity) / 2
            if similarity < 0.55:
                continue
            releases = item.get("releases", [])
            studio = any(
                release.get("release-group", {}).get("primary-type") == "Album"
                and not release.get("release-group", {}).get("secondary-types")
                for release in releases
            )
            variant = item.get("video", False) or any(
                version in item.get("disambiguation", "").lower() and version not in title.lower()
                for version in ("live", "remix", "karaoke", "demo", "mix")
            )
            for release in (releases if prefer_albums else releases[:8]) or [{}]:
                metadata = Metadata(
                    title=item["title"],
                    artist=found_artist,
                    album=release.get("title", ""),
                    date=release.get("date", ""),
                )
                results.append(
                    Candidate(
                        metadata,
                        min(0.95, similarity * 0.95),
                        "MusicBrainz text search",
                        item["id"],
                        release.get("id", ""),
                        "; ".join(
                            filter(
                                None,
                                [
                                    item.get("disambiguation", ""),
                                    release.get("status", ""),
                                    release.get("country", ""),
                                    "Text similarity, not identity probability",
                                ],
                            )
                        ),
                    )
                )
                ranks[id(results[-1])] = (-results[-1].confidence, bool(variant), not studio)
        return sorted(results, key=lambda c: ranks[id(c)] if prefer_albums else (-c.confidence,))

    def search_covers(self, artist: str, title: str, context: JobContext) -> list[Candidate]:
        """Find albums containing a strong song match, rather than search-result snippets."""
        matches = self.search(artist, title, context, prefer_albums=True)
        if not matches:
            return []
        recordings = {}
        for match in matches:
            if match.confidence >= matches[0].confidence - 0.04:
                recordings.setdefault(match.recording_id, match)
        ranked = []
        # Close recording variants are useful; broad fuzzy matches are not.
        for match in list(recordings.values())[:3]:
            offset = 0
            while True:
                context.check()
                context.progress(f"Finding original albums for {match.metadata.title}", 25)
                url = "https://musicbrainz.org/ws/2/release?" + urllib.parse.urlencode(
                    {
                        "recording": match.recording_id,
                        "inc": "release-groups+artist-credits",
                        "fmt": "json",
                        "limit": 100,
                        "offset": offset,
                    }
                )
                data = self.http.json(url, context)
                releases = data.get("releases", [])
                for release in releases:
                    group = release.get("release-group", {})
                    primary = group.get("primary-type", "")
                    secondary = group.get("secondary-types", [])
                    original_date = group.get("first-release-date", "")
                    album_artist = _artist(release.get("artist-credit", []))
                    status = release.get("status", "")
                    # A studio album precedes singles and compilation/live collections.
                    category = (
                        0
                        if primary == "Album" and not secondary
                        else (
                            1
                            if primary == "EP" and not secondary
                            else 2
                            if primary == "Single" and not secondary
                            else 3
                        )
                    )
                    rank = (
                        -match.confidence,
                        any(
                            version in match.note.lower() and version not in title.lower()
                            for version in ("live", "remix", "karaoke", "demo")
                        ),
                        status not in ("Official", ""),
                        category,
                        original_date or release.get("date") or "9999",
                        release.get("cover-art-archive", {}).get("front") is False,
                        release.get("date") or "9999",
                    )
                    note = "; ".join(
                        filter(
                            None,
                            [
                                primary,
                                ", ".join(secondary),
                                f"First released {original_date}" if original_date else "",
                                status,
                                release.get("country", ""),
                                "Text similarity, not identity probability",
                            ],
                        )
                    )
                    candidate = Candidate(
                        Metadata(
                            title=match.metadata.title,
                            artist=match.metadata.artist,
                            album=release.get("title", ""),
                            album_artist=album_artist,
                            date=release.get("date", ""),
                        ),
                        match.confidence,
                        match.source,
                        match.recording_id,
                        release.get("id", ""),
                        note,
                    )
                    if candidate.release_id:
                        ranked.append((rank, candidate))
                offset += len(releases)
                if not releases or offset >= data.get("release-count", offset):
                    break
        return merge_release_candidates(
            [candidate for _, candidate in sorted(ranked, key=lambda entry: entry[0])]
        )

    def releases(self, candidate: Candidate, context: JobContext) -> list[Candidate]:
        url = f"https://musicbrainz.org/ws/2/recording/{candidate.recording_id}?inc=releases+artist-credits&fmt=json"
        recording = self.http.json(url, context)
        result = []
        for release in recording.get("releases", []):
            metadata = Metadata(
                title=recording.get("title", candidate.metadata.title),
                artist=_artist(recording.get("artist-credit", [])) or candidate.metadata.artist,
                album=release.get("title", ""),
                date=release.get("date", ""),
            )
            note = "; ".join(
                filter(
                    None,
                    [
                        recording.get("disambiguation", ""),
                        release.get("status", ""),
                        release.get("country", ""),
                    ],
                )
            )
            result.append(
                Candidate(
                    metadata,
                    candidate.confidence,
                    candidate.source,
                    candidate.recording_id,
                    release.get("id", ""),
                    note,
                )
            )
        return sorted(
            result, key=lambda item: ("Official" not in item.note, item.metadata.date or "9999")
        ) or [candidate]

    def release(self, candidate: Candidate, context: JobContext) -> Candidate:
        if not candidate.release_id:
            return candidate
        url = f"https://musicbrainz.org/ws/2/release/{candidate.release_id}?inc=recordings+artist-credits&fmt=json"
        release = self.http.json(url, context)
        candidate.metadata.album_artist = _artist(release.get("artist-credit", []))
        candidate.metadata.date = release.get("date", candidate.metadata.date)
        for medium in release.get("media", []):
            for track in medium.get("tracks", []):
                if track.get("recording", {}).get("id") == candidate.recording_id:
                    candidate.metadata.track_number = str(track.get("number", ""))
                    return candidate
        return candidate


class LRCLibProvider:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, artist: str, title: str, context: JobContext) -> list[LyricsCandidate]:
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(
            {"artist_name": artist, "track_name": title}
        )
        records = self.http.json(url, context)
        # Deliberately discard remote synchronized timestamps for edited recordings.
        return [
            LyricsCandidate(
                item.get("trackName", ""),
                item.get("artistName", ""),
                item.get("albumName", ""),
                item["plainLyrics"],
            )
            for item in records
            if item.get("plainLyrics")
        ]


def prepare_artwork(data: bytes) -> Artwork:
    with Image.open(BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1000, 1000))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        return Artwork(buffer.getvalue(), "image/jpeg")


class CoverArtProvider:
    def __init__(self, http: HttpClient):
        self.http = http

    def fetch(self, release_id: str, context: JobContext) -> Artwork:
        context.check()
        url = f"https://coverartarchive.org/release/{release_id}/front-500"
        identity = {"release": release_id}
        stored = self.http.cache.get("artwork", identity, 30 * 86400)
        if stored:
            import base64

            return Artwork(base64.b64decode(stored["data"]), stored["mime"])
        missing_message = "This release has no front cover in Cover Art Archive"
        if self.http.cache.get("artwork_missing", identity, 6 * 3600):
            raise ValueError(missing_message)
        try:
            data = self.http.bytes(url, context)
        except RuntimeError as exc:
            if str(exc) == "Provider returned HTTP 404":
                self.http.cache.put("artwork_missing", identity, True)
                raise ValueError(missing_message) from None
            raise
        art = prepare_artwork(data)
        import base64

        self.http.cache.put(
            "artwork", identity, {"data": base64.b64encode(art.data).decode(), "mime": art.mime}
        )
        return art


class AcoustIDService:
    def __init__(self, http: HttpClient, key: str):
        self.http, self.key = http, key

    def identify(self, track: Track, context: JobContext) -> list[Candidate]:
        if not self.key:
            raise ValueError(
                "AcoustID needs an application API key in Settings; text lookup and manual editing remain available"
            )
        identity = {"content": track.content_hash}
        fingerprint = self.http.cache.get("fingerprints", identity)
        if fingerprint is None:
            fingerprint = json.loads(context.run(["fpcalc", "-json", str(track.path)]))
            self.http.cache.put("fingerprints", identity, fingerprint)
        # Send the key in POST data, not in URLs or cache/log keys.
        body = urllib.parse.urlencode(
            {
                "client": self.key,
                "duration": round(fingerprint["duration"]),
                "fingerprint": fingerprint["fingerprint"],
                "meta": "recordings",
            }
        ).encode()
        data = json.loads(self.http.bytes("https://api.acoustid.org/v2/lookup", context, body=body))
        if data.get("status") != "ok":
            raise RuntimeError("AcoustID lookup failed; check application key")
        result = []
        for match in data.get("results", []):
            for recording in match.get("recordings", []):
                metadata = Metadata(
                    title=recording.get("title", ""),
                    artist=", ".join(a["name"] for a in recording.get("artists", [])),
                )
                result.append(
                    Candidate(
                        metadata,
                        float(match["score"]),
                        "AcoustID fingerprint",
                        recording["id"],
                        note="Edited audio may reduce fingerprint reliability",
                    )
                )
        return sorted(result, key=lambda c: c.confidence, reverse=True)
