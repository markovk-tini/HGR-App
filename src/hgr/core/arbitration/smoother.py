from __future__ import annotations


class GestureSmoother:
    def __init__(self, required_frames: int = 3):
        self.required_frames = int(required_frames)
        self._candidate = 'neutral'
        self._count = 0
        self._stable = 'neutral'

    def reset(self) -> None:
        self._candidate = 'neutral'
        self._count = 0
        self._stable = 'neutral'

    def update(self, raw_gesture: str, confidence: float) -> tuple[str, int]:
        if raw_gesture == self._candidate and raw_gesture != 'neutral':
            self._count += 1
        elif raw_gesture != 'neutral':
            self._candidate = raw_gesture
            self._count = 1
        else:
            self._candidate = 'neutral'
            self._count = 0
            self._stable = 'neutral'
            return self._stable, self._count

        if self._count >= self.required_frames and confidence >= 0.60:
            self._stable = raw_gesture
        return self._stable, self._count
