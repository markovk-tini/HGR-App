from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hgr.voice.training_data import VoiceCommandDatasetBuilder


def main() -> int:
    builder = VoiceCommandDatasetBuilder()
    paths = builder.export_bundle()
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
