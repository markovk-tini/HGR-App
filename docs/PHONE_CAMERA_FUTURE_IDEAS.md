# Phone Camera — Future Feature Ideas

Captured during the phone-mic bringup work in April 2026. The
existing phone-camera pipeline (HTTPS server + AudioWorklet audio
+ JPEG frames over POST) is a reusable transport for any
"phone captures, PC processes" feature.

Ranked rough order by effort-to-value.

## Document / whiteboard scanner  *(easy, high value)*
- **Flow**: phone camera → detect page/board edges → perspective
  correct → optional OCR → save PNG/PDF in the Drawings save
  directory.
- **Reuses**: existing JPEG POST pipeline. Only new code is
  edge detection (OpenCV `findContours` + quad-approx) and a
  small UI panel.
- **OCR backend**: Tesseract (bundled alongside whisper.cpp).
  Offline, no cloud calls.
- **Why it fits Touchless**: productivity tool, ties naturally
  to the existing "save / clip" vocabulary and drawings folder.

## Barcode / QR scanner  *(trivial)*
- **Flow**: phone camera → detect barcode → decode text →
  POST to PC → copy to clipboard (or append to a notes file).
- **Libraries**: ZXing-js (browser-side) or ZBar (PC-side on
  the JPEG frames we already receive).
- **Use cases**: book/product lookups, event check-ins, quick
  URL transfer from paper to PC.

## 3D object scanning via photogrammetry  *(hard, mixed results)*
- **Flow**: user rings the phone around an object taking 30-60
  frames → PC runs structure-from-motion → export OBJ/PLY/GLB.
- **Backends**: COLMAP (most mature), Meshroom, OpenMVG.
  Reconstruction takes 5-30 min per scan on CPU, faster on GPU.
- **Quality caveat**: browser `getUserMedia` gives us JPEGs
  without IMU/depth data. Native apps (Polycam, RealityCapture)
  will always outrank this in quality because they fuse IMU +
  LiDAR on iOS Pro devices. Our version would be "good enough
  for reference, not for production assets."
- **Why defer**: large engineering lift (new pipeline, new
  binary dependency, new exporters), and the feature's ceiling
  is limited by the browser capture constraint.

## Other ideas (not yet prioritized)

- **Telestrator**: draw on the phone's camera view, stream
  annotations back to Touchless's drawing overlay. Pairs with
  the existing drawing feature.
- **Meeting / presentation assist**: detect slides, auto-snap
  current slide as a full-res image, build a post-meeting PDF.
- **Scan-to-text**: camera → OCR → dictation-style text insert
  into the active window. Variant of the document scanner but
  aimed at the text-insertion workflow rather than the file-save
  workflow.
- **Inventory / kit checklist**: phone scans barcodes against a
  user-authored list, ticks items off, flags missing ones.
