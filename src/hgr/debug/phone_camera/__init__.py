"""Embedded HTTPS + WebSocket server that lets a phone browser stream its
camera to Touchless without any app install.

The PC starts the server, presents a QR code with the LAN URL, the phone
opens the URL in its browser, `getUserMedia()` captures the rear camera,
and JPEG frames are streamed back over a WebSocket. `PhoneCameraCapture`
exposes the received frames through a `cv2.VideoCapture`-shaped API so
the existing engine camera-open path can consume them unchanged.
"""

from .capture import PhoneCameraCapture
from .server import PhoneCameraServer

__all__ = ["PhoneCameraCapture", "PhoneCameraServer"]
