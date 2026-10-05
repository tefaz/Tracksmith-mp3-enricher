"""A visual, explicit opt-in review for batch album artwork."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .layouts import WrappingLayout
from .providers import parse_filename
from .theme import tone


class CoverReviewDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review album covers")
        self.resize(1040, 760)
        self.proposals = result.proposals
        self.decisions = [None] * len(self.proposals)
        self.cards = []
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Accept the covers you want. Only accepted covers are saved when you choose "
            "Save accepted covers. Everything else is skipped. Click an accepted button again to undo."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        if result.cancelled:
            layout.addWidget(QLabel("Search stopped early. You can review the suggestions found so far."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = WrappingLayout(content, spacing=16)
        for index, proposal in enumerate(self.proposals):
            card = QWidget()
            card.setProperty("card", True)
            card.setFixedWidth(228)
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 14, 14, 14)
            box.setSpacing(8)
            image = QLabel()
            image.setFixedSize(200, 200)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(proposal.artwork.data)
            image.setPixmap(pixmap.scaled(
                200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            box.addWidget(image)
            artist, title = parse_filename(proposal.track.path.name)
            md = proposal.track.existing_metadata
            for text in (md.title or title, md.artist or artist):
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                box.addWidget(label)
            filename = QLabel(proposal.track.path.name)
            filename.setProperty("role", "muted")
            filename.setTextFormat(Qt.TextFormat.PlainText)
            filename.setWordWrap(True)
            filename.setToolTip(str(proposal.track.path))
            box.addWidget(filename)
            candidate = proposal.candidate
            details = QLabel(
                f"Suggested: {candidate.metadata.album}\n"
                f"{candidate.metadata.artist} · {candidate.metadata.title}"
            )
            details.setTextFormat(Qt.TextFormat.PlainText)
            details.setWordWrap(True)
            details.setToolTip(candidate.note)
            box.addWidget(details)
            status = QLabel("Not reviewed")
            status.setObjectName("coverReviewStatus")
            status.setProperty("role", "muted")
            box.addWidget(status)
            accept = tone(QPushButton("✓ Accept"), "positive")
            accept.setCheckable(True)
            accept.setAccessibleName(f"Accept cover for {md.title or title}")
            accept.clicked.connect(lambda checked=False, i=index: self.decide(i, checked))
            box.addWidget(accept)
            self.cards.append((accept, status))
            grid.addWidget(card)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        if result.rows:
            details = QPlainTextEdit()
            details.setReadOnly(True)
            details.setPlainText("\n".join(f"{path.name}: {message}" for path, message in result.rows))
            details.setMaximumHeight(100)
            layout.addWidget(details)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.accept_all_button = QPushButton("Accept all")
        self.accept_all_button.setAutoDefault(False)
        self.accept_all_button.setToolTip("Select every suggested cover. Files are saved only when you click Save accepted covers.")
        self.accept_all_button.clicked.connect(self.accept_all)
        buttons.addButton(self.accept_all_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Save accepted covers to MP3s")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_summary()

    @property
    def accepted_proposals(self):
        return [proposal for proposal, decision in zip(self.proposals, self.decisions) if decision is True]

    def decide(self, index, decision, refresh=True):
        self.decisions[index] = decision
        accept, status = self.cards[index]
        accept.setChecked(decision)
        accept.setText("✓ Accepted" if decision else "✓ Accept")
        accept.setToolTip("Click to undo acceptance" if decision else "Accept this cover")
        tone(accept, "neutral" if decision else "positive")
        status.setText("✓ Accepted" if decision else "Not reviewed")
        tone(status, "accepted" if decision else "")
        if refresh:
            self.update_summary()

    def accept_all(self):
        for index in range(len(self.proposals)):
            self.decide(index, True, refresh=False)
        self.update_summary()

    def update_summary(self):
        accepted = sum(value is True for value in self.decisions)
        pending = len(self.decisions) - accepted
        self.summary.setText(f"{accepted} accepted · {pending} will be skipped")
        self.save_button.setEnabled(accepted > 0)
        self.accept_all_button.setEnabled(pending > 0)
