import json
import subprocess
import sys
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer

from tracksmith import alignment_process
from tracksmith.config import Settings
from tracksmith.jobs import JobContext
from tracksmith.model import LyricLine, Word
from tracksmith.tags import read_track
from tracksmith.ui import MainWindow


def fake_child(monkeypatch, source):
    original = subprocess.Popen
    processes = []

    def spawn(command, **kwargs):
        process = original([sys.executable, "-c", source], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(alignment_process.subprocess, "Popen", spawn)
    return processes


def test_child_result_preserves_words_and_original(mp3, tmp_path, monkeypatch):
    track = read_track(mp3)
    original = mp3.read_bytes()
    line = LyricLine("Expected lyrics", 1.2345, 2, 0.9, "ai", [Word("Expected", 1.2345, 1.6, 0.9)])
    source = (
        f"print({json.dumps(json.dumps({'event': 'result', 'lines': [asdict(line)]}))}, flush=True)"
    )
    processes = fake_child(monkeypatch, source)
    result = alignment_process.run_alignment(
        track, Settings(cache_directory=str(tmp_path)), JobContext()
    )
    assert result == [line]
    assert mp3.read_bytes() == original
    assert processes[0].poll() == 0


def test_native_model_stall_keeps_ui_responsive_and_cancelable(qtbot, mp3, tmp_path, monkeypatch):
    # PyDLL holds the child's GIL for the entire native sleep, reproducing the
    # class of model import stalls that a Qt worker thread cannot isolate.
    source = """import ctypes, json
print(json.dumps({'event': 'progress', 'message': 'Load forced alignment model', 'percent': 65}), flush=True)
ctypes.PyDLL(None).sleep(30)
"""
    processes = fake_child(monkeypatch, source)
    window = MainWindow(Settings(cache_directory=str(tmp_path)))
    qtbot.addWidget(window)
    window.show()
    window.load_track(read_track(mp3))
    window.lyrics_editor.setPlainText("Expected lyrics")
    window.apply_lyrics()
    original = mp3.read_bytes()
    window.analyze()
    qtbot.waitUntil(lambda: window.job_stage == "Load forced alignment model", timeout=5000)
    assert window.progress.maximum() == 100
    assert window.progress.value() == 65
    assert "Step 5 of 6" in window.job_heading.text()
    ticks = []
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(lambda: ticks.append(True))
    heartbeat.start(10)
    qtbot.waitUntil(lambda: len(ticks) >= 5, timeout=1000)
    started = time.monotonic()
    window.cancel_job()
    qtbot.waitUntil(lambda: window.job is None, timeout=4000)
    assert time.monotonic() - started < 3
    assert processes[0].poll() is not None
    assert window.lyrics_editor.toPlainText() == "Expected lyrics"
    assert window.track.aligned_lines[0].start is None
    assert window.analyze_action.isEnabled()
    assert mp3.read_bytes() == original
    window.confirm_discard = lambda: True
    window.close()


def test_download_reports_real_cache_bytes(tmp_path, monkeypatch):
    import huggingface_hub

    from tracksmith.alignment import download_ctc_model

    info = SimpleNamespace(
        sha="revision",
        siblings=[
            SimpleNamespace(rfilename="config.json", size=128),
            SimpleNamespace(rfilename="pytorch_model.bin", size=1048576),
            SimpleNamespace(rfilename="tf_model.h5", size=1048576),
        ],
    )
    monkeypatch.setattr(huggingface_hub.HfApi, "model_info", lambda *args, **kwargs: info)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *args, **kwargs: None)
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        blobs = tmp_path / "models--example--ctc" / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "weights.incomplete").write_bytes(b"x" * 524288)
        time.sleep(0.55)
        return "snapshot"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    messages = []
    assert (
        download_ctc_model(
            "example/ctc", tmp_path, JobContext(lambda msg, percent: messages.append(msg))
        )
        == "snapshot"
    )
    assert any("0.5 / 1.0 MB" in message for message in messages)
    assert set(calls[0]["allow_patterns"]) == {"config.json", "pytorch_model.bin"}


def test_child_failure_retains_original(mp3, tmp_path, monkeypatch):
    track = read_track(mp3)
    processes = fake_child(monkeypatch, "raise RuntimeError('Model initialization failed')")
    with pytest.raises(RuntimeError, match="Model initialization failed"):
        alignment_process.run_alignment(
            track, Settings(cache_directory=str(tmp_path)), JobContext()
        )
    assert read_track(mp3).content_hash == track.content_hash
    assert processes[0].poll() != 0


def test_stalled_model_initialization_times_out_and_kills_child(mp3, tmp_path, monkeypatch):
    track = read_track(mp3)
    processes = fake_child(
        monkeypatch,
        """import ctypes, json
print(json.dumps({'event': 'progress', 'message': 'Load model', 'percent': 65}), flush=True)
ctypes.PyDLL(None).sleep(30)
""",
    )
    clock = [0]

    def progress(message, percent):
        if percent == 65:
            clock[0] = 181

    # Replace this module's time reference, not the global time module.
    monkeypatch.setattr(alignment_process, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with pytest.raises(RuntimeError, match="no progress for 3 minutes"):
        alignment_process.run_alignment(
            track, Settings(cache_directory=str(tmp_path)), JobContext(progress)
        )
    assert processes[0].poll() is not None
    assert read_track(mp3).content_hash == track.content_hash


def test_native_cpu_work_reports_activity_and_remains_cancelable(mp3, tmp_path, monkeypatch):
    from tracksmith.jobs import Cancelled

    track = read_track(mp3)
    processes = fake_child(
        monkeypatch,
        """import json
print(json.dumps({'event': 'progress', 'message': 'Recognize vocals', 'percent': 30}), flush=True)
while True:
    sum(range(10000))
""",
    )
    activity = []
    context = JobContext()

    def report(details):
        activity.append(details)
        if details["cpu_active"]:
            context.cancelled.set()

    context.activity = report
    with pytest.raises(Cancelled):
        alignment_process.run_alignment(track, Settings(cache_directory=str(tmp_path)), context)
    assert any(details["cpu_active"] for details in activity)
    assert processes[0].poll() is not None
