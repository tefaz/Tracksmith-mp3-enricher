"""Durable drafts and presentation preferences, separate from disposable caches."""

from __future__ import annotations

import base64
import json
import math
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .cache import atomic_write
from .model import Artwork, Metadata
from .timing_data import validate_lines


def encode_artwork(art):
    return (
        None
        if art is None
        else {
            "data": base64.b64encode(art.data).decode("ascii"),
            "mime": art.mime,
            "description": art.description,
        }
    )


def decode_artwork(data):
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("mime"), str):
        raise ValueError("Invalid project artwork")
    raw = base64.b64decode(data["data"], validate=True)
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("Project artwork exceeds 15 MB")
    from PySide6.QtGui import QImage

    if QImage.fromData(raw).isNull():
        raise ValueError("Project artwork is not a readable image")
    return Artwork(raw, data["mime"], data.get("description", "Cover"))


def draft_state(track):
    state = track.editable_state()
    state["artwork"] = encode_artwork(track.artwork)
    return state


def validated_state(state, duration):
    if not isinstance(state, dict) or not isinstance(state.get("lyrics"), str):
        raise ValueError("Invalid recovery lyrics")
    metadata = Metadata(**state["metadata"])
    if not all(isinstance(value, str) for value in asdict(metadata).values()):
        raise ValueError("Invalid recovery metadata")
    return {
        "metadata": asdict(metadata),
        "artwork": decode_artwork(state.get("artwork")),
        "lyrics": state["lyrics"],
        "lines": [asdict(line) for line in validate_lines(state["lines"], duration)],
    }


def apply_state(track, state):
    track.proposed_metadata = Metadata(**state["metadata"])
    track.artwork = state["artwork"]
    track.display_lyrics = state["lyrics"]
    track.aligned_lines = validate_lines(state["lines"], track.audio.duration)


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.drafts_path = self.directory / "recovery.json"
        self.preferences_path = self.directory / "presentation.json"

    def read(self, path):
        if not path.exists():
            return None
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Workspace file is too large")
        return json.loads(path.read_text())

    def preferences(self):
        try:
            value = self.read(self.preferences_path)
            if not isinstance(value, dict):
                return {}
            result = {}
            for key in ("geometry", "theme", "open_directory", "export_directory"):
                if isinstance(value.get(key), str):
                    result[key] = value[key]
            for key in ("sidebar", "panels"):
                sizes = value.get(key)
                if (
                    isinstance(sizes, list)
                    and len(sizes) == 2
                    and all(type(size) is int and 0 <= size <= 100000 for size in sizes)
                ):
                    result[key] = sizes
            for key in ("reduced_motion",):
                if type(value.get(key)) is bool:
                    result[key] = value[key]
            volume = value.get("volume")
            if type(volume) in (float, int) and 0 <= volume <= 1:
                result["volume"] = volume
            size = value.get("font_size")
            if type(size) is int and 9 <= size <= 20:
                result["font_size"] = size
            recent = value.get("recent")
            if isinstance(recent, list):
                result["recent"] = [path for path in recent if isinstance(path, str)][:12]
            return result
        except (OSError, ValueError):
            return {}

    def save_preferences(self, value):
        atomic_write(self.preferences_path, json.dumps(value).encode())

    def recovery(self):
        data = self.read(self.drafts_path)
        if data is not None and (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("songs"), list)
        ):
            raise ValueError("Unsupported or malformed recovery workspace")
        if data is not None:
            for entry in data["songs"]:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("path"), str)
                    or not entry["path"]
                    or not isinstance(entry.get("hash"), str)
                    or type(entry.get("duration")) not in (float, int)
                    or not math.isfinite(entry["duration"])
                    or entry["duration"] <= 0
                    or not isinstance(entry.get("draft"), dict)
                    or not isinstance(entry.get("baseline"), dict)
                    or type(entry.get("pending", False)) is not bool
                    or type(entry.get("position", 0)) is not int
                    or type(entry.get("selected", 0)) is not int
                ):
                    raise ValueError("Malformed song entry in recovery workspace")
        return data

    def save(self, sessions, retained=None):
        songs = deepcopy(retained or [])
        for session in sessions:
            track = session.track
            songs.append(
                {
                    "path": str(track.path),
                    "hash": track.content_hash,
                    "duration": track.audio.duration,
                    "draft": draft_state(track),
                    "baseline": {
                        **deepcopy(track._saved_state),
                        "artwork": encode_artwork(track._saved_state.get("artwork")),
                    },
                    "pending": session.lyrics_pending,
                    "position": session.playback_position,
                    "selected": session.selected_line,
                }
            )
        atomic_write(
            self.drafts_path,
            json.dumps(
                {
                    "version": 1,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "songs": songs,
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode(),
        )

    def discard(self):
        # Atomic empty workspace keeps discard semantics explicit, even after a crash.
        atomic_write(self.drafts_path, b'{"version":1,"songs":[]}')
