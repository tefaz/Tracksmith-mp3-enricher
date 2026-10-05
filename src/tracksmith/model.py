from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class Metadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    date: str = ""
    track_number: str = ""
    genre: str = ""


@dataclass
class AudioInfo:
    duration: float
    bitrate: int
    sample_rate: int
    channels: int
    id3_version: str = "none"


@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float = 0.0
    source: str = "ai"


@dataclass
class LyricLine:
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float = 0.0
    source: str = "unmatched"
    words: list[Word] = field(default_factory=list)
    note: str = ""
    line_id: str = field(default_factory=lambda: uuid4().hex, compare=False)
    line_reviewed: bool = False
    words_reviewed: bool = False
    excluded: bool = False

    def set_timestamp(self, seconds: float | None, duration: float) -> None:
        if seconds is not None and (not math.isfinite(seconds) or not 0 <= seconds <= duration):
            raise ValueError("Timestamp must be inside the audio duration")
        self.start = seconds
        self.end = None
        self.words = []
        self.confidence = 1.0 if seconds is not None else 0.0
        self.source = "manual" if seconds is not None else "unmatched"
        self.note = ""
        self.line_reviewed = seconds is not None
        self.words_reviewed = False
        self.excluded = False

    def move_timestamp(self, seconds: float | None, duration: float) -> None:
        """Move a line and its word boundaries together; never silently lose words."""
        if seconds is None or self.start is None or not self.words:
            self.set_timestamp(seconds, duration)
            return
        if not math.isfinite(seconds) or not 0 <= seconds <= duration:
            raise ValueError("Timestamp must be inside the audio duration")
        delta = seconds - self.start
        if any(not 0 <= word.start + delta < word.end + delta <= duration for word in self.words):
            raise ValueError("Moving this line would put word timings outside the audio")
        for word in self.words:
            word.start += delta
            word.end += delta
        self.start = seconds
        self.end = self.words[-1].end
        self.confidence = 1.0
        self.source = "manual"
        self.line_reviewed = True
        self.words_reviewed = False
        self.note = "Line moved manually; word boundaries shifted by the same amount. Review words separately."


@dataclass
class Artwork:
    data: bytes
    mime: str
    description: str = "Cover"


@dataclass
class Candidate:
    metadata: Metadata
    confidence: float
    source: str
    recording_id: str = ""
    release_id: str = ""
    note: str = ""


@dataclass
class Track:
    path: Path
    audio: AudioInfo
    existing_metadata: Metadata
    proposed_metadata: Metadata
    content_hash: str
    artwork: Artwork | None = None
    display_lyrics: str = ""
    aligned_lines: list[LyricLine] = field(default_factory=list)
    identification_candidates: list[Candidate] = field(default_factory=list)
    lyrics_frame_key: str | None = None
    sync_frame_key: str | None = None
    artwork_frame_key: str | None = None
    lyrics_language: str = "eng"
    sync_language: str = "eng"
    status: str = "Not analyzed"
    _saved_state: dict = field(default_factory=dict, repr=False)

    @property
    def aligned_words(self) -> list[Word]:
        return [word for line in self.aligned_lines for word in line.words]

    @property
    def alignment_lyrics(self) -> list[str]:
        from .lyrics import alignment_text

        return [alignment_text(line.text) for line in self.aligned_lines]

    def editable_state(self) -> dict:
        return {
            "metadata": asdict(self.proposed_metadata),
            "artwork": self.artwork,
            "lyrics": self.display_lyrics,
            "lines": [asdict(x) for x in self.aligned_lines],
        }

    def mark_saved(self) -> None:
        self._saved_state = deepcopy(self.editable_state())

    @property
    def dirty(self) -> bool:
        return self.editable_state() != self._saved_state


def active_line(lines: list[LyricLine], position: float) -> int | None:
    timed = [
        (line.start, index)
        for index, line in enumerate(lines)
        if not line.excluded and line.start is not None and line.start <= position
    ]
    if not timed:
        return None
    _, index = max(timed)
    line = lines[index]
    if line.end is not None and position > line.end:
        return None
    return index


def needs_review(line: LyricLine) -> bool:
    return not line.excluded and (
        line.start is None
        or not line.line_reviewed
        or (bool(line.words) and not line.words_reviewed)
    )


def review_summary(lines: list[LyricLine]) -> dict[str, int]:
    return {
        "total": len(lines),
        "timed": sum(line.start is not None and not line.excluded for line in lines),
        "missing": sum(line.start is None and not line.excluded for line in lines),
        "excluded": sum(line.excluded for line in lines),
        "review": sum(needs_review(line) for line in lines),
        "words": sum(len(line.words) for line in lines if not line.excluded),
        "estimates": sum(word.source == "estimated" for line in lines for word in line.words),
    }
