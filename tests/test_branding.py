import json

from mutagen.id3 import ID3, SYLT, USLT

from tracksmith.config import Settings, config_path
from tracksmith.jobs import JobContext
from tracksmith.tags import compressed_audio_hash, read_track, save_track


def isolated_storage(monkeypatch, tmp_path):
    for kind in ("CONFIG", "DATA", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / kind.lower()))


def test_new_install_uses_tracksmith_storage(monkeypatch, tmp_path):
    isolated_storage(monkeypatch, tmp_path)
    settings = Settings.load()
    assert config_path() == tmp_path / "config/tracksmith/settings.json"
    assert settings.workspace_directory == str(tmp_path / "data/tracksmith")
    assert settings.cache_directory == str(tmp_path / "cache/tracksmith")


def test_legacy_settings_and_storage_remain_available(monkeypatch, tmp_path):
    isolated_storage(monkeypatch, tmp_path)
    legacy = tmp_path / "config/song-metadata-enricher/settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"language": "de"}))
    for kind in ("cache", "data"):
        (tmp_path / kind / "song-metadata-enricher").mkdir(parents=True)
    settings = Settings.load()
    assert settings.language == "de"
    assert settings.workspace_directory == str(tmp_path / "data/song-metadata-enricher")
    assert settings.cache_directory == str(tmp_path / "cache/song-metadata-enricher")
    settings.save()
    assert config_path().exists()
    assert config_path().stat().st_mode & 0o777 == 0o600
    assert legacy.exists()
    settings.language = "fr"
    settings.save()
    assert Settings.load().language == "fr"


def test_legacy_lyric_frames_are_replaced_without_touching_other_frames(mp3):
    tags = ID3(mp3)
    tags.add(USLT(encoding=3, lang="eng", desc="", text="other lyrics"))
    tags.add(USLT(encoding=3, lang="eng", desc="Song Metadata Enricher", text="Original"))
    tags.add(
        SYLT(
            encoding=3,
            lang="eng",
            desc="Song Metadata Enricher",
            format=2,
            type=1,
            text=[("\nOriginal", 500)],
        )
    )
    tags.save(mp3)
    original = mp3.read_bytes()
    audio = compressed_audio_hash(mp3, JobContext())
    track = read_track(mp3)
    assert track.display_lyrics == "Original"
    assert track.aligned_lines[0].start == 0.5
    assert mp3.read_bytes() == original
    track.display_lyrics = "Updated"
    track.aligned_lines[0].text = "Updated"
    saved = save_track(track)
    tags = ID3(mp3)
    assert {f.desc for f in tags.getall("USLT")} == {"", "Tracksmith"}
    assert {f.desc for f in tags.getall("SYLT")} == {"Tracksmith"}
    assert saved.display_lyrics == "Updated"
    assert compressed_audio_hash(mp3, JobContext()) == audio
