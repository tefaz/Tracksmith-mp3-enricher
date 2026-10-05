"""Exercise both installers without installing system files or using personal settings."""

import os
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE_CODE = r"""
import os
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from tracksmith import __version__
from tracksmith.config import Settings
from tracksmith.model import LyricLine, Word
from tracksmith.tags import read_track, save_track
from tracksmith.ui import MainWindow

with tempfile.TemporaryDirectory(prefix="tracksmith-package-test-") as temporary:
    root = Path(temporary)
    os.environ["XDG_CONFIG_HOME"] = str(root / "config")
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    source = root / "test.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=4", "-c:a", "libmp3lame", str(source)], check=True)
    track = read_track(source)
    track.display_lyrics = "Hello world\nLine only"
    track.aligned_lines = [
        LyricLine("Hello world", 0.5, 1.5, words=[Word("Hello", 0.5, 0.8), Word("world", 1, 1.5)]),
        LyricLine("Line only", 2.5),
    ]
    save_track(track)
    copied = root / "copied.mp3"
    copied.write_bytes(source.read_bytes())
    assert read_track(copied).aligned_lines == track.aligned_lines
    app = QApplication([])
    window = MainWindow(Settings(cache_directory=str(root / "cache"),
                                 workspace_directory=str(root / "data")))
    assert window.workspace_tabs.count() == 2
    window.confirm_discard = lambda: True
    window.close()
print("PASS bundled GUI and portable line/word timing save, version " + __version__)
"""


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "APPIMAGELAUNCHER_DISABLE": "1"}
    appimage = ROOT / "dist" / f"Tracksmith-{version}-x86_64.AppImage"
    subprocess.run(
        [str(appimage), "--appimage-extract-and-run", "--python", "-c", SMOKE_CODE],
        env=environment,
        check=True,
        timeout=60,
    )
    deb = ROOT / "dist" / f"tracksmith_{version}_amd64.deb"
    with tempfile.TemporaryDirectory(prefix="tracksmith-deb-test-") as temporary:
        root = Path(temporary)
        process = subprocess.Popen(["ar", "p", str(deb), "data.tar.gz"], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|gz") as tar:
                tar.extractall(root, filter="data")
        finally:
            process.stdout.close()
            if process.wait() != 0:
                raise RuntimeError("Could not extract the Debian installer")
        subprocess.run(
            [str(root / "usr/bin/tracksmith"), "--python", "-c", SMOKE_CODE],
            env=environment,
            check=True,
            timeout=60,
        )


if __name__ == "__main__":
    main()
