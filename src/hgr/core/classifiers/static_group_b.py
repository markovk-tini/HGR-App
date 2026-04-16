from __future__ import annotations

from .gesture_types import StaticGestureScores, avg, clamp01, closed
from ..features.static_features import StaticFeatures



def _thumb_fold_bonus(features: StaticFeatures) -> float:
    thumb_closed = max(closed(features.open_scores['thumb']), clamp01((features.curl_scores['thumb'] - 0.42) / 0.24))
    thumb_clear = clamp01((features.spread_ratios['thumb_index'] - 0.24) / 0.30)
    return clamp01(0.65 * thumb_closed + 0.35 * thumb_clear)



def score_group_b(features: StaticFeatures) -> StaticGestureScores:
    s = features.open_scores
    ext = features.extension_scores
    curl = features.curl_scores
    spread = features.spread_ratios
    thumb_fold = _thumb_fold_bonus(features)

    index_middle_pair = clamp01((spread['index_middle'] - 0.18) / 0.34)
    ring_cluster = clamp01((0.40 - avg(spread[key] for key in ('middle_ring', 'ring_pinky'))) / 0.22)
    low_tail = avg(max(closed(s[name]), clamp01((curl[name] - 0.44) / 0.26)) for name in ('ring', 'pinky'))
    low_pinky = max(closed(s['pinky']), clamp01((curl['pinky'] - 0.44) / 0.26))
    isolated_index = clamp01((max(s['index'], ext['index']) - max(s['middle'], s['ring'], s['pinky'])) / 0.18)
    two_split = clamp01((avg((max(s['index'], ext['index']), max(s['middle'], ext['middle']))) - avg((s['ring'], s['pinky']))) / 0.22)
    three_split = clamp01((avg((s['index'], s['middle'], s['ring'])) - s['pinky']) / 0.18)
    four_thumb_penalty = max(clamp01((0.70 - s['thumb']) / 0.16), clamp01((curl['thumb'] - 0.42) / 0.24))
    four_thumb_closed = max(closed(s['thumb']), clamp01((curl['thumb'] - 0.44) / 0.24))

    two_apart_gate = clamp01((spread['index_middle'] - 0.42) / 0.14)
    two_together_penalty = clamp01((0.38 - spread['index_middle']) / 0.14)
    two_pair_open = avg((max(s['index'], ext['index']), max(s['middle'], ext['middle'])))
    two_ring_fold = avg(max(closed(s[name]), clamp01((curl[name] - 0.42) / 0.28)) for name in ('ring', 'pinky'))

    return {
        'one': clamp01(
            0.34 * max(s['index'], ext['index'])
            + 0.16 * isolated_index
            + 0.18 * avg(max(closed(s[name]), clamp01((curl[name] - 0.42) / 0.28)) for name in ('middle', 'ring', 'pinky'))
            + 0.14 * thumb_fold
            + 0.18 * clamp01((0.70 - max(s['middle'], ext['middle'])) / 0.24)
        ),
        'two': clamp01(
            (
                0.34 * two_pair_open
                + 0.18 * two_ring_fold
                + 0.14 * thumb_fold
                + 0.16 * two_split
                + 0.18 * two_apart_gate
            )
            * (1.00 - 0.72 * two_together_penalty)
        ),
        'three': clamp01(
            0.34 * avg((max(s['index'], ext['index']), max(s['middle'], ext['middle']), max(s['ring'], ext['ring'] * 0.92)))
            + 0.18 * low_pinky
            + 0.16 * thumb_fold
            + 0.16 * three_split
            + 0.16 * clamp01((spread['ring_pinky'] - 0.22) / 0.34)
        ),
        'four': clamp01(
            0.36 * avg((max(s['index'], ext['index']), max(s['middle'], ext['middle']), max(s['ring'], ext['ring']), max(s['pinky'], ext['pinky'])))
            + 0.28 * four_thumb_penalty
            + 0.16 * four_thumb_closed
            + 0.10 * ring_cluster
            + 0.10 * clamp01((spread['index_middle'] + spread['middle_ring'] + spread['ring_pinky'] - 0.44) / 0.40)
        ),
    }
