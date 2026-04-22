from __future__ import annotations

from .gesture_types import StaticGestureScores, avg, clamp01, closed
from ..features.static_features import StaticFeatures



def score_special(features: StaticFeatures) -> StaticGestureScores:
    s = features.open_scores
    ext = features.extension_scores
    curl = features.curl_scores
    hook = features.hook_scores
    spread = features.spread_ratios
    states = features.states

    adjacent_open = avg(s[name] for name in ('index', 'middle', 'ring', 'pinky'))
    close_adjacent = avg(features.spread_together_strengths[key] for key in ('index_middle', 'middle_ring', 'ring_pinky'))
    wide_adjacent = avg(features.spread_apart_strengths[key] for key in ('index_middle', 'middle_ring', 'ring_pinky'))

    thumb_open_gate = max(clamp01((s['thumb'] - 0.56) / 0.22), clamp01((ext['thumb'] - 0.54) / 0.24))
    pinky_hook_gate = hook['pinky']
    pinky_open_gate = max(clamp01((s['pinky'] - 0.54) / 0.20), clamp01((ext['pinky'] - 0.50) / 0.22), 0.78 * pinky_hook_gate)
    folded_rest_gate = avg(
        max(clamp01((0.66 - s[name]) / 0.24), clamp01((curl[name] - 0.42) / 0.22), 0.72 * hook[name])
        for name in ('index', 'middle', 'ring')
    )
    rest_open_penalty = avg(max(clamp01((s[name] - 0.50) / 0.22), clamp01((ext[name] - 0.48) / 0.22)) for name in ('index', 'middle', 'ring'))
    mute_balance_gate = clamp01((thumb_open_gate + pinky_open_gate - max(s['index'], s['middle'], s['ring']) - 0.62) / 0.24)
    mute_span_gate = clamp01((spread['thumb_index'] - 0.36) / 0.14)
    mute_core = 0.42 * avg((thumb_open_gate, pinky_open_gate)) + 0.30 * folded_rest_gate + 0.18 * mute_balance_gate + 0.10 * mute_span_gate
    mute_shape = clamp01((s['thumb'] + s['pinky'] - max(s['index'], s['middle'], s['ring']) - 0.72) / 0.18)
    thumb_anchor = clamp01((features.pinch_ratios['thumb_index'] - 0.26) / 0.26)

    volume_index_gate = clamp01((s['index'] - 0.72) / 0.12)
    volume_middle_gate = clamp01((s['middle'] - 0.72) / 0.12)
    volume_ring_fold = clamp01((0.62 - s['ring']) / 0.14)
    volume_pinky_fold = clamp01((0.62 - s['pinky']) / 0.14)
    volume_thumb_fold = clamp01((0.72 - s['thumb']) / 0.18)
    volume_together_gate = clamp01((features.spread_together_strengths['index_middle'] - 0.60) / 0.22)
    volume_not_apart_gate = clamp01((0.34 - features.spread_apart_strengths['index_middle']) / 0.22)
    volume_shape_gate = avg((volume_index_gate, volume_middle_gate, volume_ring_fold, volume_pinky_fold, volume_thumb_fold))
    volume_confidence_gate = avg(
        (
            features.finger_state_confidences['index'],
            features.finger_state_confidences['middle'],
            features.finger_state_confidences['ring'],
            features.finger_state_confidences['pinky'],
        )
    )
    volume_state_gate = avg(
        (
            1.0 if states['index'] == 'open' else 0.0,
            1.0 if states['middle'] == 'open' else 0.0,
            1.0 if states['ring'] == 'closed' else 0.30 if states['ring'] == 'unknown' else 0.0,
            1.0 if states['pinky'] == 'closed' else 0.30 if states['pinky'] == 'unknown' else 0.0,
            1.0 if states['thumb'] == 'closed' else 0.40 if states['thumb'] == 'unknown' else 0.0,
        )
    )
    volume_fold_gate = clamp01((volume_ring_fold + volume_pinky_fold + volume_thumb_fold - 1.10) / 0.40)
    volume_spacing_gate = 0.72 * volume_together_gate + 0.28 * volume_not_apart_gate
    if features.spread_states.get('index_middle') != 'together':
        volume_spacing_gate = 0.0

    return {
        'mute': clamp01(
            (0.54 * mute_core + 0.18 * mute_shape + 0.10 * thumb_anchor + 0.18 * mute_span_gate)
            * (0.24 + 0.76 * folded_rest_gate)
            * (0.30 + 0.70 * mute_balance_gate)
            * (1.00 - 0.88 * rest_open_penalty)
        ),
        'volume_pose': clamp01(
            (0.58 * volume_shape_gate + 0.18 * volume_confidence_gate + 0.24 * volume_state_gate)
            * (0.18 + 0.82 * volume_spacing_gate)
            * (0.28 + 0.72 * volume_fold_gate)
        ),
        'finger_together': clamp01(0.42 * adjacent_open + 0.58 * close_adjacent),
        'finger_apart': clamp01(0.42 * adjacent_open + 0.58 * wide_adjacent),
    }
