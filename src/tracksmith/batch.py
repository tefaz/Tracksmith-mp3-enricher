"""Folder batch operations, independent of widgets."""

import math
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .alignment_process import run_alignment
from .cover_batch import CoverSkipped
from .jobs import Cancelled, JobContext
from .lyrics import alignment_text, normalize_text, split_lyrics
from .providers import parse_filename
from .tags import read_track, save_track

CATEGORIES = (
    "No embedded lyrics",
    "Embedded lyrics but missing line timings",
    "Embedded lyrics and line timings but some missing word timings",
    "fully timed",
)


def timing_category(track):
    """Classify the saved baseline, so unsaved drafts never count as embedded."""
    state = track._saved_state or track.editable_state()
    if not state.get("lyrics", "").strip():
        return CATEGORIES[0]
    lines = [line for line in state.get("lines", []) if not line.get("excluded", False)]
    if not lines or any(line.get("start") is None for line in lines):
        return CATEGORIES[1]
    for line in lines:
        words = line.get("words", [])
        expected = alignment_text(line["text"])
        actual = normalize_text(" ".join(word["text"] for word in words))
        if not words or not expected or actual != expected or len(words) != len(expected.split()):
            return CATEGORIES[2]
        previous_end = None
        for word in words:
            start, end = word["start"], word["end"]
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or not 0 <= start < end <= track.audio.duration
                or (previous_end is not None and start < previous_end)
            ):
                return CATEGORIES[2]
            previous_end = end
    return CATEGORIES[3]


def folder_mp3s(folder: Path, recursive=False):
    paths = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (path for path in paths if path.is_file() and path.suffix.lower() == ".mp3"),
        key=lambda path: str(path).casefold(),
    )


def best_lyrics(track, candidates):
    fallback_artist, fallback_title = parse_filename(track.path.name)
    metadata = track.proposed_metadata
    artist = normalize_text(metadata.artist or fallback_artist)
    title = normalize_text(metadata.title or fallback_title)
    if not artist or not title:
        return None
    ranked = []
    for candidate in candidates:
        if not split_lyrics(candidate.lyrics):
            continue
        artist_score = SequenceMatcher(None, artist, normalize_text(candidate.artist)).ratio()
        title_score = SequenceMatcher(None, title, normalize_text(candidate.title)).ratio()
        if artist_score < 0.8 or title_score < 0.8:
            continue
        album_score = (
            SequenceMatcher(
                None, normalize_text(metadata.album), normalize_text(candidate.album)
            ).ratio()
            if metadata.album
            else 0
        )
        ranked.append((0.45 * artist_score + 0.55 * title_score, album_score, candidate))
    ranked.sort(key=lambda item: item[:2], reverse=True)
    return ranked[0][2] if ranked else None


@dataclass
class BatchResult:
    saved: list = field(default_factory=list)
    rows: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False


def run_batch(tracks, mode, settings, provider, context, replace=False, on_saved=None):
    """Commit one file at a time; return completed commits even after cancellation."""
    result = BatchResult()
    for index, original in enumerate(tracks):
        prefix = f"{index + 1}/{len(tracks)} · {original.path.name}"
        child = JobContext(
            lambda text, percent: context.progress(
                f"{prefix} · {text}", int((index * 100 + percent) / len(tracks))
            ),
            context.activity,
        )
        child.cancelled = context.cancelled
        try:
            child.check()
            track = deepcopy(original)
            if track.dirty:
                result.rows.append((track.path, "Skipped: unsaved edits"))
                continue
            if mode == "covers":
                # Read disk again: any APIC, even a back cover, prevents replacement.
                embedded = read_track(track.path, child)
                if embedded.artwork is not None:
                    result.rows.append((track.path, "Skipped: already has embedded artwork"))
                    continue
                if embedded.content_hash != track.content_hash:
                    result.rows.append((track.path, "Skipped: file changed; reopen it first"))
                    continue
                if track.artwork is not None:
                    result.rows.append((track.path, "Skipped: already has artwork"))
                    continue
                track.artwork, album = provider.lookup(track, child)
            elif mode == "lyrics":
                if track.display_lyrics.strip() and not replace:
                    result.rows.append((track.path, "Skipped: already has lyrics"))
                    continue
                artist, title = parse_filename(track.path.name)
                artist = track.proposed_metadata.artist or artist
                title = track.proposed_metadata.title or title
                if not artist or not title:
                    result.rows.append((track.path, "Skipped: artist/title needed for lookup"))
                    continue
                child.progress("Searching lyrics", 5)
                candidate = best_lyrics(track, provider.search(artist, title, child))
                if candidate is None:
                    result.rows.append((track.path, "No sufficiently close lyric match"))
                    continue
                lines = split_lyrics(candidate.lyrics, track.aligned_lines)
                retained = {line.line_id for line in lines}
                if any(
                    (line.start is not None or line.excluded) and line.line_id not in retained
                    for line in track.aligned_lines
                ):
                    result.rows.append(
                        (track.path, "Skipped: replacement would remove timed/excluded lines")
                    )
                    continue
                track.display_lyrics = candidate.lyrics
                track.aligned_lines = lines
            else:
                if not track.display_lyrics.strip() or not track.aligned_lines:
                    result.rows.append((track.path, "Skipped: no saved sung lyrics"))
                    continue
                if any(line.start is not None for line in track.aligned_lines):
                    result.rows.append((track.path, "Skipped: already has line timings"))
                    continue
                old_lines = track.aligned_lines
                track.aligned_lines = run_alignment(track, settings, child)
                # Keep occurrence identity and intentional exclusions from the saved draft.
                from collections import defaultdict, deque

                previous = defaultdict(deque)
                for line in old_lines:
                    previous[line.text].append(line)
                for line in track.aligned_lines:
                    if previous[line.text]:
                        old = previous[line.text].popleft()
                        line.line_id = old.line_id
                        if old.excluded:
                            line.set_timestamp(None, track.audio.duration)
                            line.excluded = True
                if not any(
                    line.start is not None and not line.excluded for line in track.aligned_lines
                ):
                    result.rows.append((track.path, "No reliable line timings found"))
                    continue
            saved = save_track(track, context=child)
            result.saved.append(saved)
            timed = sum(
                line.start is not None and not line.excluded for line in saved.aligned_lines
            )
            message = (
                f"Cover saved: {album}"
                if mode == "covers"
                else "Lyrics saved"
                if mode == "lyrics"
                else f"Timings saved: {timed} lines; listening review needed"
            )
            if on_saved:
                try:
                    on_saved(saved)
                except Exception as exc:
                    message += f"; timing storage failed: {exc}"
            result.rows.append((track.path, message))
        except CoverSkipped as exc:
            result.rows.append((original.path, f"Skipped: {exc}"))
        except Cancelled:
            result.cancelled = True
            result.rows.append((original.path, "Cancelled: file not saved"))
            break
        except Exception as exc:
            result.rows.append((original.path, f"Failed: {exc}"))
    if context.cancelled.is_set():
        result.cancelled = True
    return result
