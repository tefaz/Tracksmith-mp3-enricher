"""In-memory working state for a loaded song; independent of Qt."""

from dataclasses import dataclass, field

from .model import Candidate, LyricLine, Track


@dataclass
class SongSession:
    track: Track
    lyrics_pending: bool = False
    release_id: str = ""
    release_candidates: list[Candidate] = field(default_factory=list)
    playback_position: int = 0
    selected_line: int = 0
    stamp_history: list[tuple[int, LyricLine, LyricLine]] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    redo: list[dict] = field(default_factory=list)
