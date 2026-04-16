from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..features.static_features import StaticFeatures


@dataclass(frozen=True)
class GesturePrediction:
    raw_gesture: str
    confidence: float
    candidate_scores: Dict[str, float]


def _avg(values):
    vals = list(values)
    return sum(vals) / max(1, len(vals))


def _closed(score: float) -> float:
    return max(0.0, min(1.0, 1.0 - score))


def classify_static(features: StaticFeatures) -> GesturePrediction:
    s = features.open_scores
    spread = features.spread_ratios
    pinch = features.pinch_ratios

    open_non_thumb = _avg([s['index'], s['middle'], s['ring'], s['pinky']])
    closed_non_thumb = _avg([_closed(s['index']), _closed(s['middle']), _closed(s['ring']), _closed(s['pinky'])])
    three_fingers = _avg([s['middle'], s['ring'], s['pinky']])

    scores: Dict[str, float] = {}
    scores['open_hand'] = 0.76 * _avg(s.values()) + 0.24 * _avg([
        min(1.0, spread['thumb_index'] / 0.95),
        min(1.0, spread['index_middle'] / 0.72),
        min(1.0, spread['middle_ring'] / 0.62),
        min(1.0, spread['ring_pinky'] / 0.58),
    ])
    scores['fist'] = 0.82 * _avg([_closed(v) for v in s.values()]) + 0.18 * min(1.0, 0.55 / max(spread['thumb_index'], 1e-6)) * 0.25
    scores['one'] = _avg([s['index'], _closed(s['middle']), _closed(s['ring']), _closed(s['pinky']), _closed(s['thumb'])])
    scores['two'] = _avg([s['index'], s['middle'], _closed(s['ring']), _closed(s['pinky']), _closed(s['thumb'])])
    scores['three'] = _avg([s['index'], s['middle'], s['ring'], _closed(s['pinky']), _closed(s['thumb'])])
    scores['four'] = _avg([s['index'], s['middle'], s['ring'], s['pinky'], _closed(s['thumb'])])

    zero_pinch = max(0.0, min(1.0, (0.38 - pinch['thumb_index']) / 0.24))
    scores['zero'] = 0.48 * zero_pinch + 0.52 * _avg([s['middle'], s['ring'], s['pinky'], max(s['index'], 0.40)])
    scores['mute'] = 0.64 * _avg([s['thumb'], s['pinky'], _closed(s['index']), _closed(s['middle']), _closed(s['ring'])]) + 0.36 * max(0.0, min(1.0, (pinch['thumb_index'] - 0.30) / 0.55))

    if s['thumb'] > 0.55:
        scores['four'] *= 0.78
        scores['three'] *= 0.88
        scores['two'] *= 0.92
    if open_non_thumb < 0.58:
        scores['open_hand'] *= 0.72
    if closed_non_thumb < 0.56:
        scores['fist'] *= 0.74
    if pinch['thumb_index'] < 0.28:
        scores['open_hand'] *= 0.82
        scores['fist'] *= 0.82
        scores['zero'] *= 1.08
    if three_fingers < 0.55:
        scores['zero'] *= 0.70

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    confidence = max(0.0, min(1.0, 0.72 * best_score + 0.28 * max(0.0, margin + 0.10)))
    if best_score < 0.58 or margin < 0.035:
        return GesturePrediction(raw_gesture='neutral', confidence=float(max(best_score, confidence) * 0.65), candidate_scores=scores)
    return GesturePrediction(raw_gesture=best_label, confidence=float(confidence), candidate_scores=scores)
