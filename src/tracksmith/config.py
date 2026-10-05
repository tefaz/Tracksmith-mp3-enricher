import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CPU_THREAD_LIMIT = 4


def config_path() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "tracksmith/settings.json"
    )


def storage_directory(variable: str, fallback: str) -> str:
    """Keep existing drafts, models and timings reachable after the app rename."""
    root = Path(os.environ.get(variable, Path.home() / fallback))
    current = root / "tracksmith"
    legacy = root / "song-metadata-enricher"
    return str(legacy if not current.exists() and legacy.exists() else current)


@dataclass
class Settings:
    cache_directory: str = field(
        default_factory=lambda: storage_directory("XDG_CACHE_HOME", ".cache")
    )
    lyrics_provider: str = "lrclib"
    metadata_provider: str = "musicbrainz"
    ai_backend: str = "whisper-attention"
    whisper_model: str = "small"
    device: str = "cpu"
    language: str = "auto"
    ctc_model: str = ""
    separate_vocals: bool = False
    confidence_threshold: float = 0.8
    acoustid_key: str = ""
    workspace_directory: str = field(
        default_factory=lambda: storage_directory("XDG_DATA_HOME", ".local/share")
    )

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if key == "separate_vocals":
                if type(value) is not bool:
                    raise ValueError(f"{key} must be a boolean")
            elif key == "confidence_threshold":
                if type(value) not in {float, int} or not 0 <= value <= 1:
                    raise ValueError("Confidence threshold must be between 0 and 1")
            elif not isinstance(value, str):
                raise ValueError(f"{key} must be text")
        if not self.cache_directory:
            raise ValueError("Cache directory cannot be empty")
        if not self.workspace_directory:
            raise ValueError("Draft and corrected timing directory cannot be empty")
        self.cache_directory = str(Path(self.cache_directory).expanduser())
        self.workspace_directory = str(Path(self.workspace_directory).expanduser())
        if self.device not in {"cpu", "auto", "rocm"}:
            raise ValueError("Device must be cpu, auto, or rocm")

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        if path is None:
            path = config_path()
            if not path.exists():
                legacy = path.parent.parent / "song-metadata-enricher/settings.json"
                if legacy.exists():
                    path = legacy
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Settings must contain a JSON object")
        settings = cls(**{key: val for key, val in data.items() if key in cls.__dataclass_fields__})
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        from .cache import atomic_write

        atomic_write(path, json.dumps(asdict(self), indent=2).encode(), mode=0o600)
