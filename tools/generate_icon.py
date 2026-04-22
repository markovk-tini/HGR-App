"""Generate the Touchless app icon from the user's hand-drawn source PNG.

Pipeline:
  1. Load `assets/icons/hand_source.png`.
  2. Threshold the green drawing against the dark background to get a binary mask.
  3. Morphologically close small holes, then smooth edges with a blur + threshold pass
     so the freehand roughness is gone but silhouette stays crisp.
  4. One last light Gaussian blur to produce an anti-aliased alpha channel.
  5. Crop to the hand's bounding box, fit it centered inside the panel with padding.
  6. Paint the silhouette in the app's accent green (#1DE9B6) and composite onto the
     deep-blue rounded panel with teal border.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024

PANEL_FILL = (26, 73, 163, 255)      # deep blue
PANEL_BORDER = (38, 236, 196, 255)   # teal accent
HAND_ACCENT = (29, 233, 182, 255)    # #1DE9B6 — app accent color
HAND_PAD_RATIO = 0.11                # empty margin inside the panel, each side

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "icons"
SOURCE_PATH = ASSETS / "hand_source.png"


def _binary_mask_from_source(source: Image.Image) -> Image.Image:
    """Return an L-mode binary mask (0/255) of the green hand pixels."""
    rgb = np.asarray(source.convert("RGB"), dtype=np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # Hand is clearly green: G dominant vs R and B, and bright enough to exclude background.
    is_hand = (g > 80) & (g > r + 15) & (g > b + 15)
    binary = np.where(is_hand, 255, 0).astype(np.uint8)
    return Image.fromarray(binary, mode="L")


def _clean_and_smooth(binary: Image.Image) -> Image.Image:
    """Close small holes, smooth jagged freehand edges, return an anti-aliased alpha mask."""
    # Close pinholes inside the silhouette: dilate-then-erode.
    closed = binary.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    # Remove tiny specks outside: erode-then-dilate.
    opened = closed.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    # Smooth ragged edges by blurring then re-thresholding.
    smoothed = opened.filter(ImageFilter.GaussianBlur(radius=4))
    smoothed = smoothed.point(lambda v: 255 if v >= 128 else 0, mode="L")
    # Final light blur → anti-aliased alpha for crisp-but-soft edges when scaled.
    return smoothed.filter(ImageFilter.GaussianBlur(radius=1.2))


def _fit_mask_to_canvas(alpha: Image.Image, canvas_size: int, pad_ratio: float) -> Image.Image:
    """Crop the mask to its silhouette bbox, scale (keeping aspect) into a padded canvas."""
    bbox = alpha.getbbox()
    if bbox is None:
        return Image.new("L", (canvas_size, canvas_size), 0)
    cropped = alpha.crop(bbox)
    inner = int(canvas_size * (1.0 - 2.0 * pad_ratio))
    scale = min(inner / cropped.width, inner / cropped.height)
    new_w = max(1, int(round(cropped.width * scale)))
    new_h = max(1, int(round(cropped.height * scale)))
    resized = cropped.resize((new_w, new_h), resample=Image.LANCZOS)
    canvas = Image.new("L", (canvas_size, canvas_size), 0)
    off_x = (canvas_size - new_w) // 2
    off_y = (canvas_size - new_h) // 2
    canvas.paste(resized, (off_x, off_y))
    return canvas


def _build_panel() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 32
    panel_rect = (margin, margin, SIZE - margin, SIZE - margin)
    draw.rounded_rectangle(
        panel_rect,
        radius=160,
        fill=PANEL_FILL,
        outline=PANEL_BORDER,
        width=22,
    )
    return img


def build_icon() -> Image.Image:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Hand source PNG not found at {SOURCE_PATH}")
    source = Image.open(SOURCE_PATH)
    binary = _binary_mask_from_source(source)
    alpha = _clean_and_smooth(binary)
    hand_mask = _fit_mask_to_canvas(alpha, SIZE, HAND_PAD_RATIO)

    panel = _build_panel()
    ink = Image.new("RGBA", (SIZE, SIZE), HAND_ACCENT)
    panel.paste(ink, (0, 0), hand_mask)
    return panel


def main() -> None:
    img = build_icon()
    ASSETS.mkdir(parents=True, exist_ok=True)
    png_path = ASSETS / "touchless_icon.png"
    img.save(png_path, format="PNG")
    print(f"Wrote {png_path}")

    ico_path = ASSETS / "touchless_icon.ico"
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()
