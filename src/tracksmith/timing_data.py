"""Rich timing snapshots supplement the line-only ID3 representation."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

from .cache import Cache
from .lyrics import alignment_reference, timed_lines
from .model import LyricLine, Track, Word


@dataclass(frozen=True)
class TimingWindow:
    start: float
    end: float
    word_start: float
    anchored: bool


def word_timing_window(lines: list[LyricLine], row: int, duration: float) -> TimingWindow:
    """An untimed line belongs between its surrounding timed lyric rows."""
    line = lines[row]
    previous = next((item for item in reversed(lines[:row]) if item.start is not None), None)
    following = next((item for item in lines[row + 1 :] if item.start is not None), None)
    start = line.start if line.start is not None else previous.start if previous else 0.0
    next_start = (
        next_line_start(lines, row)
        if line.start is not None
        else following.start
        if following
        else None
    )
    end = next_start if next_start is not None else duration
    if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end <= duration:
        raise ValueError(
            "Neighbouring line timestamps are out of order or outside the audio. Correct them before editing these words."
        )
    word_start = start
    if line.start is None and previous:
        previous_end = previous.end
        if previous_end is None and previous.words:
            previous_end = previous.words[-1].end
        if previous_end is not None and start <= previous_end < end:
            word_start = previous_end
    return TimingWindow(
        start,
        end,
        word_start,
        line.start is not None or previous is not None or following is not None,
    )


def next_line_start(lines: list[LyricLine], row: int) -> float | None:
    start = lines[row].start
    if start is None:
        return None
    # Edited/reordered lyrics may differ from row order: use the next start in audio time.
    return min(
        (
            line.start
            for index, line in enumerate(lines)
            if index != row and line.start is not None and line.start > start
        ),
        default=None,
    )


def pending_line_at(lines: list[LyricLine], position: float, duration: float) -> int | None:
    """Choose an untimed lyric in the current audio section, never a timed row.

    Missing rows share their surrounding anchors; take their earliest occurrence
    in that section. Sections already passed are left untimed, not stamped later.
    """
    candidates = []
    for row, line in enumerate(lines):
        if line.start is not None or line.excluded:
            continue
        try:
            window = word_timing_window(lines, row, duration)
        except ValueError:
            continue
        if window.start <= position < window.end or position == window.end == duration:
            candidates.append((window.start, -row, row))
    return max(candidates)[2] if candidates else None


def evenly_spaced_words(text: str, start: float, end: float, duration: float) -> list[Word]:
    tokens = alignment_reference(text).split()
    if not tokens:
        raise ValueError("This line has no words to space")
    if (
        not all(math.isfinite(value) for value in (start, end, duration))
        or not 0 <= start < end <= duration
    ):
        raise ValueError("Choose a start before the end, inside the audio duration")
    width = (end - start) / len(tokens)
    return [
        Word(
            token,
            start + index * width,
            end if index == len(tokens) - 1 else start + (index + 1) * width,
            0.0,
            "estimated",
        )
        for index, token in enumerate(tokens)
    ]


def generate_timing_json(track: Track) -> str:
    """Lossless portable word/line data; plain LRC and current SYLT are line-only."""
    from .workspace import encode_artwork

    return (
        json.dumps(
            {
                "schema_version": 2,
                "source": {
                    "filename": track.path.name,
                    "content_sha256": track.content_hash,
                    "duration": track.audio.duration,
                },
                "metadata": asdict(track.proposed_metadata),
                "display_lyrics": track.display_lyrics,
                "lines": [asdict(line) for line in track.aligned_lines],
                "artwork": encode_artwork(track.artwork),
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def store_timing_state(track: Track, cache: Cache):
    """Called after an explicit MP3 save, keyed to the resulting file bytes."""
    cache.put(
        "saved_timings",
        {"content": track.content_hash, "version": 1},
        {"lyrics": track.display_lyrics, "lines": [asdict(line) for line in track.aligned_lines]},
    )


def restore_timing_state(track: Track, cache: Cache) -> bool:
    data = cache.get("saved_timings", {"content": track.content_hash, "version": 1})
    try:
        if not data or data["lyrics"] != track.display_lyrics:
            return False
        lines = validate_lines(data["lines"], track.audio.duration)
        for line in lines:
            if (
                not isinstance(line.text, str)
                or not math.isfinite(line.confidence)
                or not 0 <= line.confidence <= 1
            ):
                return False
            if any(
                value is not None
                and (not math.isfinite(value) or not 0 <= value <= track.audio.duration)
                for value in (line.start, line.end)
            ):
                return False
            if line.words and (line.start is None or line.end is None):
                return False
            for word in line.words:
                if word.source not in {"ai", "manual", "mixed", "estimated"}:
                    return False
                if not isinstance(word.text, str) or not all(
                    math.isfinite(value) for value in (word.start, word.end, word.confidence)
                ):
                    return False
                if (
                    not 0 <= word.start < word.end <= track.audio.duration
                    or not 0 <= word.confidence <= 1
                ):
                    return False

        def embedded(rows):
            return [(line.text, round(line.start * 1000)) for line in timed_lines(rows)]

        if embedded(lines) != embedded(track.aligned_lines):
            return False
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False
    track.aligned_lines = lines
    track.mark_saved()
    return True


def validate_lines(data, duration: float) -> list[LyricLine]:
    """Validate imported/recovered data completely before mutating a working track."""
    if not isinstance(data, list) or len(data) > 100000:
        raise ValueError("Timing data must contain a bounded list of lyric lines")
    lines = []
    ids = set()
    for raw in data:
        if not isinstance(raw, dict):
            raise ValueError("Invalid lyric line")
        row = dict(raw)
        if "line_reviewed" not in row:
            row["line_reviewed"] = row.get("source") == "manual"
        try:
            row["words"] = [Word(**word) for word in row.get("words", [])]
            line = LyricLine(**row)
        except (TypeError, KeyError) as exc:
            raise ValueError("Unsupported or malformed lyric/word fields") from exc
        if not isinstance(line.text, str) or not isinstance(line.source, str):
            raise ValueError("Lyric text and provenance must be text")
        if not isinstance(line.line_id, str) or not line.line_id or line.line_id in ids:
            raise ValueError("Lyric occurrence IDs must be unique")
        ids.add(line.line_id)
        if any(
            type(getattr(line, key)) is not bool
            for key in ("line_reviewed", "words_reviewed", "excluded")
        ):
            raise ValueError("Review and exclusion flags must be booleans")
        if (
            type(line.confidence) not in (int, float)
            or not math.isfinite(line.confidence)
            or not 0 <= line.confidence <= 1
        ):
            raise ValueError("Invalid line support")
        for value in (line.start, line.end):
            if value is not None and (
                type(value) not in (float, int)
                or not math.isfinite(value)
                or not 0 <= value <= duration
            ):
                raise ValueError("Line timestamp is outside the audio")
        if line.end is not None and (line.start is None or line.end <= line.start):
            raise ValueError("Line end must follow its start")
        previous_end = None
        for word in line.words:
            if not isinstance(word.text, str) or word.source not in {
                "ai",
                "manual",
                "mixed",
                "estimated",
                "untimed",
            }:
                raise ValueError("Invalid word text or provenance")
            if any(
                type(value) not in (float, int) or not math.isfinite(value)
                for value in (word.start, word.end, word.confidence)
            ):
                raise ValueError("Word timings and support must be finite numbers")
            if not 0 <= word.start < word.end <= duration or not 0 <= word.confidence <= 1:
                raise ValueError("Invalid word boundaries or support")
            if previous_end is not None and word.start < previous_end:
                raise ValueError("Word spans overlap or are out of order")
            previous_end = word.end
        if line.words and (line.start != line.words[0].start or line.end != line.words[-1].end):
            raise ValueError("Line bounds must agree with its words")
        if line.excluded and (line.start is not None or line.words):
            raise ValueError("Excluded lyrics must remain untimed")
        lines.append(line)
    return lines


def parse_timing_project(text: str, track: Track) -> dict:
    data = json.loads(text)
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] not in (1, 2)
    ):
        raise ValueError("Unsupported timing project version")
    source = data.get("source", {})
    if (
        source.get("content_sha256") != track.content_hash
        or source.get("duration") != track.audio.duration
    ):
        raise ValueError(
            "This project belongs to different or changed audio. Open its original MP3 first."
        )
    from .model import Metadata

    metadata = Metadata(**data.get("metadata", {}))
    if not all(isinstance(value, str) for value in asdict(metadata).values()) or not isinstance(
        data.get("display_lyrics"), str
    ):
        raise ValueError("Project metadata and lyrics must be text")
    lines = validate_lines(data.get("lines"), track.audio.duration)
    return {"metadata": metadata, "lyrics": data["display_lyrics"], "lines": lines}
