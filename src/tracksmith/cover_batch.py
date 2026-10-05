"""Read-only cover suggestions and optional conservative audio verification."""

import math
import shutil
import urllib.parse
from copy import deepcopy
from dataclasses import dataclass, field

from .jobs import Cancelled, JobContext
from .lyrics import normalize_text
from .model import Artwork, Candidate, Metadata, Track
from .providers import (
    AcoustIDService,
    CoverArtProvider,
    MusicBrainzProvider,
    _artist,
    parse_filename,
    prepare_artwork,
)
from .tags import read_track


class CoverSkipped(ValueError):
    """An expected lack of evidence, rather than a provider failure."""


class BatchCoverProvider:
    def __init__(self, http, key="", fingerprint=False):
        self.http = http
        self.identification = AcoustIDService(http, key)
        self.fingerprint = fingerprint

    def recommend(self, track, context):
        """Suggestions may be fuzzy: the user reviews every image before saving."""
        artist, title = parse_filename(track.path.name)
        artist = track.existing_metadata.artist or artist
        title = track.existing_metadata.title or title
        if self.fingerprint:
            artwork, album = self.lookup(track, context)
            return artwork, Candidate(
                Metadata(artist=artist, title=title, album=album), 0,
                "Audio identity verified",
            )
        if not normalize_text(artist) or not normalize_text(title):
            raise CoverSkipped("artist and title needed for cover suggestions")
        context.progress("Searching album cover suggestions", 5)
        candidates = MusicBrainzProvider(self.http).search_covers(artist, title, context)
        covers = CoverArtProvider(self.http)
        tried = set()
        for candidate in candidates:
            context.check()
            if not candidate.release_id or candidate.release_id in tried:
                continue
            if len(tried) >= 8:
                break
            tried.add(candidate.release_id)
            context.progress(f"Previewing {candidate.metadata.album}", 60)
            try:
                return covers.fetch(candidate.release_id, context), candidate
            except ValueError:
                # Missing covers are common; try another ranked album edition.
                continue
        raise CoverSkipped("no album cover suggestion available")

    def lookup(self, track, context):
        if self.fingerprint and (not self.identification.key or not shutil.which("fpcalc")):
            raise CoverSkipped("AcoustID application key and fpcalc are required")
        if track.audio.duration < 30:
            raise CoverSkipped("short audio clip (under 30 seconds)")
        fallback_artist, fallback_title = parse_filename(track.path.name)
        artist = track.existing_metadata.artist or fallback_artist
        title = track.existing_metadata.title or fallback_title
        if not normalize_text(artist) or not normalize_text(title):
            raise CoverSkipped("artist and title needed for cover lookup")
        context.progress("Checking audio fingerprint" if self.fingerprint else "Searching artist and title", 5)
        matches = (
            self.identification.identify(track, context)
            if self.fingerprint
            else MusicBrainzProvider(self.http).search(artist, title, context, prefer_albums=True)
        )
        if not self.fingerprint:
            matches = [
                match for match in matches
                if normalize_text(match.metadata.artist) == normalize_text(artist)
                and normalize_text(match.metadata.title) == normalize_text(title)
            ]
        scores = {}
        for match in matches:
            if match.recording_id and math.isfinite(match.confidence):
                scores[match.recording_id] = max(
                    scores.get(match.recording_id, 0), match.confidence
                )
        ranked = sorted(scores, key=scores.get, reverse=True)
        if not ranked or scores[ranked[0]] < (0.95 if self.fingerprint else 0.90):
            raise CoverSkipped("no strong audio fingerprint match" if self.fingerprint else "no exact artist/title match")
        if len(ranked) > 1 and (
            not self.fingerprint or scores[ranked[1]] >= scores[ranked[0]] - 0.10
        ):
            raise CoverSkipped("ambiguous audio fingerprint matches" if self.fingerprint else "artist/title matches several recordings")
        recording_id = ranked[0]
        recording = self.http.json(
            f"https://musicbrainz.org/ws/2/recording/{recording_id}?inc=artist-credits&fmt=json",
            context,
        )
        if (
            normalize_text(recording.get("title", "")) != normalize_text(title)
            or normalize_text(_artist(recording.get("artist-credit", [])))
            != normalize_text(artist)
        ):
            raise CoverSkipped("recording identity disagrees with artist/title")
        if recording.get("video") or recording.get("disambiguation", "").strip():
            raise CoverSkipped("recording version needs manual review")
        context.progress("Finding original studio album", 25)
        releases, offset = [], 0
        while True:
            context.check()
            query = urllib.parse.urlencode({
                "recording": recording_id, "inc": "release-groups+artist-credits",
                "fmt": "json", "limit": 100, "offset": offset,
            })
            data = self.http.json("https://musicbrainz.org/ws/2/release?" + query, context)
            page = data.get("releases", [])
            releases.extend(page)
            offset += len(page)
            if not page or offset >= data.get("release-count", offset):
                break
        release = original_album(releases, artist, track.existing_metadata.album)
        context.progress("Checking approved front cover", 50)
        try:
            archive = self.http.json(
                f"https://coverartarchive.org/release/{release['id']}", context
            )
        except RuntimeError as exc:
            if str(exc) == "Provider returned HTTP 404":
                raise CoverSkipped("original album has no archived cover") from None
            raise
        fronts = [
            image for image in archive.get("images", [])
            if image.get("front") is True and image.get("approved") is True
        ]
        if len(fronts) != 1:
            raise CoverSkipped("no single approved front cover")
        image = fronts[0]
        url = image.get("thumbnails", {}).get("500") or image.get("image", "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CoverSkipped("front cover has no secure download URL")
        context.progress("Downloading verified album cover", 65)
        try:
            artwork = prepare_artwork(self.http.bytes(url, context))
        except RuntimeError as exc:
            if str(exc) == "Provider returned HTTP 404":
                raise CoverSkipped("approved front cover is unavailable") from None
            raise
        return artwork, release["title"]


@dataclass
class CoverProposal:
    track: Track
    artwork: Artwork
    candidate: Candidate


@dataclass
class CoverSearchResult:
    proposals: list[CoverProposal] = field(default_factory=list)
    rows: list = field(default_factory=list)
    cancelled: bool = False


def find_cover_proposals(tracks, provider, context):
    """Read/search only. Cancellation retains previews already found, never saves."""
    result = CoverSearchResult()
    for index, original in enumerate(tracks):
        prefix = f"{index + 1}/{len(tracks)} · {original.path.name}"
        child = JobContext(
            lambda text, percent: context.progress(
                f"{prefix} · {text}", int((index * 100 + max(0, percent)) / len(tracks))
            ), context.activity,
        )
        child.cancelled = context.cancelled
        try:
            child.check()
            if original.dirty:
                raise CoverSkipped("unsaved edits")
            embedded = read_track(original.path, child)
            if embedded.artwork is not None or original.artwork is not None:
                raise CoverSkipped("already has embedded artwork")
            if embedded.content_hash != original.content_hash:
                raise CoverSkipped("file changed; reopen it first")
            artwork, candidate = provider.recommend(original, child)
            child.check()
            result.proposals.append(CoverProposal(deepcopy(original), artwork, candidate))
        except CoverSkipped as exc:
            result.rows.append((original.path, f"Skipped: {exc}"))
        except Cancelled:
            result.cancelled = True
            break
        except Exception as exc:
            result.rows.append((original.path, f"Failed: {exc}"))
    result.cancelled = result.cancelled or context.cancelled.is_set()
    return result


class AcceptedCoverProvider:
    """Save exactly the reviewed images; never perform a second online lookup."""

    def __init__(self, proposals):
        self.proposals = {proposal.track.path: proposal for proposal in proposals}

    def lookup(self, track, context):
        context.check()
        proposal = self.proposals[track.path]
        return proposal.artwork, proposal.candidate.metadata.album


def original_album(releases, artist, album=""):
    """Require a unique earliest studio album and an original-year edition."""
    albums = []
    for release in releases:
        group = release.get("release-group", {})
        if (
            release.get("status") == "Official"
            and group.get("primary-type") == "Album"
            and not group.get("secondary-types")
            and normalize_text(_artist(release.get("artist-credit", []))) == normalize_text(artist)
        ):
            date = group.get("first-release-date", "")
            if not group.get("id") or len(date) < 4 or not date[:4].isdigit():
                raise CoverSkipped("album chronology is incomplete")
            albums.append(release)
    if not albums:
        raise CoverSkipped("no official studio album confirmed")
    earliest_year = min(r["release-group"]["first-release-date"][:4] for r in albums)
    earliest = [r for r in albums if r["release-group"]["first-release-date"][:4] == earliest_year]
    if len({r["release-group"]["id"] for r in earliest}) != 1:
        raise CoverSkipped("original album is ambiguous")
    if album and normalize_text(album) != normalize_text(earliest[0]["release-group"].get("title", "")):
        raise CoverSkipped("original album disagrees with embedded album tag")
    editions = [
        r for r in earliest
        if r.get("date", "")[:4] == earliest_year
        and not r.get("disambiguation", "").strip()
        and r.get("cover-art-archive", {}).get("front") is True
        and normalize_text(r.get("title", ""))
        == normalize_text(r["release-group"].get("title", ""))
    ]
    if not editions:
        raise CoverSkipped("no original-year album edition with a front cover")
    # Several regional editions of the same original album are acceptable.
    return min(editions, key=lambda r: (r["date"], r["id"]))
