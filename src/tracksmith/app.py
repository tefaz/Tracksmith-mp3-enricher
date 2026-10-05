import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import Settings
from .theme import apply_theme
from .ui import MainWindow


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("stage", "seconds", "backend", "device", "model", "versions"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def main():
    parser = argparse.ArgumentParser(description="Tracksmith — MP3 metadata and local lyric synchronization")
    parser.add_argument("mp3", nargs="?", type=Path)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Tracksmith")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "app-icon.png")))
    app.setDesktopFileName("tracksmith")
    app.setOrganizationName("Tracksmith")
    apply_theme(app)
    try:
        settings = Settings.load()
    except (ValueError, OSError) as exc:
        QMessageBox.warning(
            None, "Settings could not be loaded", f"Using defaults for this session: {exc}"
        )
        settings = Settings()
    directory = Path(settings.cache_directory)
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "application.jsonl", maxBytes=2_000_000, backupCount=3
    )
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, handlers=[handler])
    window = MainWindow(settings)
    window.show()

    def startup():
        window.offer_recovery()
        if args.mp3:
            if window.job:
                window._pending_startup_path = args.mp3
            else:
                window.open_path(args.mp3)

    QTimer.singleShot(0, startup)
    return app.exec()
