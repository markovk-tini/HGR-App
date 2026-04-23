"""Modal that owns the phone-camera server's lifecycle for the "Connect via QR" flow.

Opens a secondary window showing a scannable QR (and the URL as text for
typing), watches server status callbacks, and lets the user commit the
phone stream as the Touchless camera source once frames are flowing. On
cancel or window-close we tear the server down so nothing keeps running
unattended.
"""
from __future__ import annotations

import io
from typing import Optional

import qrcode
from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...config.app_config import AppConfig
from ...debug.phone_camera import PhoneCameraServer


def _pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def _build_qr_pixmap(url: str, pixel_size: int = 320) -> QPixmap:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img = img.resize((pixel_size, pixel_size), Image.NEAREST)
    return _pil_to_qpixmap(img)


class PhoneCameraConnectDialog(QDialog):
    """Runs the phone-camera server for the duration of the dialog.

    Emits `camera_accepted` when the user confirms they want to use the
    running phone stream as the Touchless camera source. If the dialog is
    canceled or closed, the server is stopped and `camera_accepted` is
    NOT emitted — the caller should keep using whatever source was
    active before.
    """

    camera_accepted = Signal(object)  # PhoneCameraServer

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._server = PhoneCameraServer(port=8765, on_status=self._on_server_status)
        self._server_info = None
        self._streaming_seen = False
        self._committed = False

        self.setWindowTitle("Connect Phone Camera")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._apply_theme()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("Scan this QR code with your phone camera")
        title.setObjectName("phoneDialogTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        subtitle = QLabel(
            "Your phone must be on the same WiFi network as this PC. Open the QR in your phone's default browser."
        )
        subtitle.setObjectName("phoneDialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumHeight(320)
        layout.addWidget(self.qr_label)

        self.url_label = QLabel("starting server...")
        self.url_label.setObjectName("phoneDialogUrl")
        self.url_label.setAlignment(Qt.AlignCenter)
        self.url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        layout.addWidget(self.url_label)

        warn = QLabel(
            "Your browser will warn that the connection is not private because Touchless uses a self-signed "
            "certificate. Tap \"Show Details\" → \"visit this website\" (Safari) or \"Advanced\" → \"Proceed\" "
            "(Chrome) to continue. Then tap Allow when the browser asks to use your camera."
        )
        warn.setObjectName("phoneDialogWarn")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        self.status_label = QLabel("Waiting for phone...")
        self.status_label.setObjectName("phoneDialogStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("phoneDialogButton")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

        self.use_button = QPushButton("Use This Camera")
        self.use_button.setObjectName("phoneDialogPrimary")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._on_use_clicked)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)

        # Server start + QR render happen right after the dialog is shown
        # so UI paints first (QR generation is trivial but the cert file
        # IO can occasionally stall for a beat on first run).
        QTimer.singleShot(0, self._start_server)

        # Poll every second for "did we see a frame recently" — the
        # streaming callback only fires once per client, but we want to
        # reflect ongoing connectivity.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            PhoneCameraConnectDialog {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
            }}
            QLabel#phoneDialogTitle {{
                color: {self.config.text_color};
                font-size: 17px;
                font-weight: 600;
            }}
            QLabel#phoneDialogSubtitle {{
                color: {self.config.text_color};
                font-size: 13px;
                opacity: 0.85;
            }}
            QLabel#phoneDialogUrl {{
                color: {self.config.accent_color};
                font-size: 15px;
                font-weight: 600;
                padding: 8px 4px;
                background: rgba(255,255,255,0.05);
                border-radius: 8px;
            }}
            QLabel#phoneDialogWarn {{
                color: {self.config.text_color};
                font-size: 12px;
                opacity: 0.75;
            }}
            QLabel#phoneDialogStatus {{
                color: {self.config.text_color};
                font-size: 13px;
                font-weight: 600;
                padding: 8px;
                background: rgba(255,255,255,0.05);
                border-radius: 8px;
            }}
            QPushButton#phoneDialogButton {{
                background-color: rgba(255,255,255,0.08);
                color: {self.config.text_color};
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 10px;
                padding: 8px 18px;
                min-width: 96px;
            }}
            QPushButton#phoneDialogButton:hover {{
                background-color: rgba(255,255,255,0.15);
            }}
            QPushButton#phoneDialogPrimary {{
                background-color: {self.config.accent_color};
                color: #003d2a;
                border: none;
                border-radius: 10px;
                padding: 8px 18px;
                min-width: 140px;
                font-weight: 600;
            }}
            QPushButton#phoneDialogPrimary:disabled {{
                background-color: rgba(29,233,182,0.4);
                color: rgba(0,61,42,0.6);
            }}
            """
        )

    def _start_server(self) -> None:
        try:
            info = self._server.start()
        except Exception as exc:
            self.url_label.setText(f"Failed to start server: {type(exc).__name__}: {exc}")
            self.status_label.setText("Server could not start. Close and try again.")
            return
        self._server_info = info
        self.url_label.setText(info.url)
        try:
            pix = _build_qr_pixmap(info.url, pixel_size=320)
            self.qr_label.setPixmap(pix)
        except Exception as exc:
            self.qr_label.setText(f"(could not render QR: {exc})")

    def _on_server_status(self, event: str, data: dict) -> None:
        # Status callbacks arrive on the server thread. Marshal onto the
        # GUI thread via a queued connection pattern: QTimer.singleShot(0).
        def _apply():
            if event == "phone_page_loaded":
                # HTML page loaded — phone reaches the PC over HTTPS.
                # If WSS then fails to connect, the cause is almost
                # certainly cert-trust on iOS (Safari doesn't carry over
                # the "Visit Website" exception to WSS).
                if not self._streaming_seen:
                    self.status_label.setText(
                        f"Phone loaded the Touchless page. Waiting for camera connection..."
                    )
            elif event == "client_connected":
                self.status_label.setText("Phone browser opened the video stream. Waiting for frames...")
            elif event == "streaming":
                self._streaming_seen = True
                self.status_label.setText("Streaming — phone camera is live.")
                self.use_button.setEnabled(True)
            elif event == "client_disconnected":
                self.status_label.setText("Phone disconnected.")
                self._streaming_seen = False
                self.use_button.setEnabled(False)
        QTimer.singleShot(0, _apply)

    def _refresh_status(self) -> None:
        if self._committed:
            return
        if not self._streaming_seen:
            return
        stale = self._server.seconds_since_last_frame
        if stale > 4.0:
            self.status_label.setText(f"Stream appears stalled (no frame for {stale:.0f}s).")
            self.use_button.setEnabled(False)
        else:
            self.status_label.setText(f"Streaming — last frame {stale*1000:.0f} ms ago.")
            self.use_button.setEnabled(True)

    def _on_use_clicked(self) -> None:
        self._committed = True
        self.camera_accepted.emit(self._server)
        self.accept()

    def closeEvent(self, event) -> None:
        # If the user closed the dialog without clicking "Use This Camera",
        # tear the server down — nothing should be left listening on the
        # LAN if they never committed.
        if not self._committed:
            try:
                self._server.stop()
            except Exception:
                pass
        super().closeEvent(event)

    def reject(self) -> None:  # type: ignore[override]
        # Called for Cancel button / ESC — same cleanup as closeEvent.
        if not self._committed:
            try:
                self._server.stop()
            except Exception:
                pass
        super().reject()

    @property
    def server(self) -> Optional[PhoneCameraServer]:
        return self._server
