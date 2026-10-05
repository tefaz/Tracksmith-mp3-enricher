"""Review dialogs with scrollable content and always-reachable actions."""

from dataclasses import fields

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .model import Metadata, review_summary
from .theme import tone


def scroll_content(layout):
    content = QWidget()
    body = QVBoxLayout(content)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(content)
    area.setMinimumSize(0, 0)
    layout.addWidget(area, 1)
    return body


class MetadataProposalDialog(QDialog):
    def __init__(self, current, proposed, parent=None, cover=None, allow_extra=False):
        super().__init__(parent)
        self.setWindowTitle("Choose details to apply")
        self.resize(760, 480)
        root = QVBoxLayout(self)
        body = scroll_content(root)
        body.addWidget(QLabel("Choose the fields to replace. Existing values are kept by default."))
        self.checks = {}
        table = QTableWidget(7, 3)
        table.setHorizontalHeaderLabels(["Apply field", "Current", "Proposed"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(280)
        for row, item in enumerate(fields(Metadata)):
            before, after = getattr(current, item.name), getattr(proposed, item.name)
            check = QCheckBox(item.name.replace("_", " ").title())
            check.setEnabled(bool(after) and before != after)
            check.setChecked(bool(after) and not before)
            self.checks[item.name] = check
            table.setCellWidget(row, 0, check)
            for col, value in ((1, before), (2, after)):
                cell = QTableWidgetItem(value or "(empty)")
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, cell)
        table.resizeColumnsToContents()
        body.addWidget(table)
        fill = QPushButton("Fill empty fields")
        fill.clicked.connect(
            lambda: [
                check.setChecked(bool(getattr(proposed, key)) and not getattr(current, key))
                for key, check in self.checks.items()
            ]
        )
        body.addWidget(fill)
        self.cover_check = QCheckBox("Replace current cover with the selected cover")
        self.cover_check.setChecked(False)
        self.cover_check.setVisible(cover is not None)
        body.addWidget(self.cover_check)
        self.extra_check = QCheckBox("Fill initially empty fields from extra release details")
        self.extra_check.setChecked(allow_extra)
        self.extra_check.setVisible(allow_extra)
        self.extra_fields = (
            {
                item.name
                for item in fields(Metadata)
                if not getattr(current, item.name) and not getattr(proposed, item.name)
            }
            if allow_extra
            else set()
        )
        body.addWidget(self.extra_check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply selected fields")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_fields(self):
        selected = {
            key for key, check in self.checks.items() if check.isEnabled() and check.isChecked()
        }
        return selected | (self.extra_fields if self.extra_check.isChecked() else set())


class SaveReviewDialog(QDialog):
    def __init__(self, track, proposed, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review changes before saving")
        self.resize(760, 560)
        root = QVBoxLayout(self)
        target = QLabel(f"Save changes to {track.path.name}\n{track.path}")
        target.setTextFormat(Qt.TextFormat.PlainText)
        target.setWordWrap(True)
        root.addWidget(target)
        body = scroll_content(root)
        table = QTableWidget(len(proposed), 1)
        table.setHorizontalHeaderLabels(["Changes"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(160)
        for row, text in enumerate(proposed):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, item)
        body.addWidget(table)
        covers = QHBoxLayout()
        for name, art in (
            ("Saved cover", track._saved_state.get("artwork")),
            ("Proposed cover", track.artwork),
        ):
            box = QVBoxLayout()
            box.addWidget(QLabel(name))
            label = QLabel("No cover")
            if art:
                pixmap = QPixmap()
                pixmap.loadFromData(art.data)
                label.setPixmap(
                    pixmap.scaled(
                        100,
                        100,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            box.addWidget(label)
            covers.addLayout(box)
        body.addLayout(covers)
        s = review_summary(track.aligned_lines)
        summary = QLabel(
            f"{s['timed']} timed lines · {s['missing']} unresolved omissions · {s['excluded']} intentionally excluded · {s['review']} need listening review\n\nMP3: song details, cover, plain lyrics, line timings and word timings.\nApplication storage: additional timing/review data for compatibility.\nTiming project (.json): portable copy that can be reopened.\n\nAudio is preserved without re-encoding. Output ID3 version: 2.4."
        )
        summary.setWordWrap(True)
        body.addWidget(summary)
        actions = QHBoxLayout()
        back = QPushButton("Back to review")
        back.clicked.connect(self.reject)
        actions.addWidget(back)
        actions.addStretch()
        save = tone(QPushButton("Save changes to this MP3"), "positive")
        save.clicked.connect(self.accept)
        actions.addWidget(save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        root.addLayout(actions)


class SearchHintsDialog(QDialog):
    def __init__(self, artist, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search terms")
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Lookup terms only; your edited song details are kept."))
        self.artist = QLineEdit(artist)
        self.title = QLineEdit(title)
        for name, edit in (("Artist", self.artist), ("Song title", self.title)):
            label = QLabel(name)
            label.setBuddy(edit)
            root.addWidget(label)
            root.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Search")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
