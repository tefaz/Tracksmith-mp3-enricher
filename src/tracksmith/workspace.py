"""Presentation preferences and shared editing/project state helpers."""

from __future__ import annotations

import base64
import json
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


def apply_state(track, state):
    track.proposed_metadata = Metadata(**state["metadata"])
    track.artwork = state["artwork"]
    track.display_lyrics = state["lyrics"]
    track.aligned_lines = validate_lines(state["lines"], track.audio.duration)


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory)
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
