from __future__ import annotations

from typing import Dict, Tuple

from .gesture_types import GesturePrediction, StaticGestureDetector, avg, clamp01
from .static_group_a import score_group_a
from .static_group_b import score_group_b
from .static_special import score_special
from ..features.static_features import StaticFeatures


STATIC_DETECTORS: tuple[StaticGestureDetector, ...] = (
    score_group_a,
    score_group_b,
    score_special,
)

ACTUAL_GESTURE_LABELS: tuple[str, ...] = (
    'open_hand',
    'fist',
    'zero',
    'one',
    'two',
    'three',
    'four',
    'mute',
    'volume_pose',
)

DEBUG_SPACING_LABELS: tuple[str, ...] = (
    'finger_together',
    'finger_apart',
)



def score_static_candidates(features: StaticFeatures) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for detector in STATIC_DETECTORS:
        for name, score in detector(features).items():
            scores[name] = clamp01(score)

    for label in ACTUAL_GESTURE_LABELS + DEBUG_SPACING_LABELS:
        scores.setdefault(label, 0.0)

    s = features.open_scores
    ext = features.extension_scores
    curl = features.curl_scores
    hook = features.hook_scores
    pinch = features.pinch_ratios
    spread = features.spread_ratios

    non_thumb_open = avg(s[name] for name in ('index', 'middle', 'ring', 'pinky'))
    non_thumb_ext = avg(ext[name] for name in ('index', 'middle', 'ring', 'pinky'))
    non_thumb_curl = avg(curl[name] for name in ('index', 'middle', 'ring', 'pinky'))
    curled_non_thumb = avg(max(curl[name], 0.76 * hook[name]) for name in ('index', 'middle', 'ring', 'pinky'))
    hook_profile = avg(hook[name] for name in ('index', 'middle', 'ring', 'pinky'))
    compact_cluster = avg(clamp01((0.44 - spread[key]) / 0.18) for key in ('index_middle', 'middle_ring', 'ring_pinky'))
    tail_open = avg(s[name] for name in ('middle', 'ring', 'pinky'))
    tail_closed = avg(1.0 - s[name] for name in ('middle', 'ring', 'pinky'))
    pinky_shape_open = max(
        s['pinky'],
        clamp01((features.tip_palm_ratios['pinky'] - features.pip_palm_ratios['pinky'] - 0.22) / 0.24),
        clamp01((ext['pinky'] - 0.50) / 0.22),
    )
    four_non_thumb = min(s[name] for name in ('index', 'middle', 'ring', 'pinky'))
    fist_fold_profile = avg(
        clamp01((features.bend_angles[name][0] - 148.0) / 24.0)
        * clamp01((116.0 - features.bend_angles[name][1]) / 36.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    fist_retraction = avg(
        clamp01((0.05 - (features.wrist_tip_ratios[name] - features.wrist_pip_ratios[name])) / 0.14)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    open_hand_gate = clamp01((min(s['thumb'], four_non_thumb) - 0.62) / 0.14)
    folded_thumb_gate = max(clamp01((0.62 - s['thumb']) / 0.18), clamp01((curl['thumb'] - 0.38) / 0.28))
    claw_shape_profile = avg(
        clamp01((features.bend_angles[name][0] - 154.0) / 18.0)
        * clamp01((132.0 - features.bend_angles[name][1]) / 52.0)
        * clamp01((features.wrist_tip_ratios[name] - features.wrist_pip_ratios[name] - 0.04) / 0.18)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    claw_profile = max(hook_profile, claw_shape_profile)
    curled_shell_penalty = avg(
        clamp01((100.0 - features.bend_angles[name][0]) / 40.0)
        * clamp01((features.bend_angles[name][1] - 104.0) / 24.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    compact_hook_fist_gate = clamp01((compact_cluster + fist_retraction + clamp01((curled_non_thumb - 0.54) / 0.30) - 1.05) / 0.48)
    false_claw_gate = (
        clamp01((claw_profile - 0.34) / 0.28)
        * clamp01((non_thumb_ext - 0.40) / 0.24)
        * clamp01((0.34 - compact_cluster) / 0.22)
    )
    open_rest_gate = avg(max(s[name], ext[name]) for name in ('index', 'middle', 'ring'))
    mute_rest_fold_gate = avg(max(clamp01((0.64 - s[name]) / 0.24), clamp01((curl[name] - 0.40) / 0.22), 0.72 * hook[name]) for name in ('index', 'middle', 'ring'))
    volume_tail_fold_gate = avg(
        (
            max(clamp01((curl['ring'] - 0.36) / 0.24), clamp01((0.62 - s['ring']) / 0.20), 0.74 * hook['ring']),
            max(clamp01((curl['pinky'] - 0.36) / 0.24), clamp01((0.62 - s['pinky']) / 0.20), 0.74 * hook['pinky']),
        )
    )

    if open_hand_gate > 0.0:
        scores['open_hand'] = clamp01(scores['open_hand'] + 0.22 * open_hand_gate)
    if features.states['thumb'] == 'open' and four_non_thumb > 0.74:
        scores['open_hand'] = clamp01(max(scores['open_hand'], 0.74 + 0.14 * open_hand_gate))
        scores['four'] *= 0.20
    elif s['thumb'] > 0.62 and four_non_thumb > 0.80:
        scores['open_hand'] = clamp01(max(scores['open_hand'], 0.70 + 0.10 * open_hand_gate))
        scores['four'] *= 0.42
    elif s['thumb'] > 0.60:
        scores['four'] *= 0.52 + 0.24 * folded_thumb_gate
    if features.states['thumb'] == 'open' and s['thumb'] > 0.68 and four_non_thumb > 0.78:
        scores['four'] *= 0.30

    if s['thumb'] < 0.70:
        scores['open_hand'] *= 0.82
    if non_thumb_open < 0.62:
        for label in ('open_hand', 'four', 'finger_together', 'finger_apart'):
            scores[label] *= 0.58
    if s['middle'] < 0.68:
        scores['three'] *= 0.80
        scores['four'] *= 0.72
    if s['ring'] < 0.68:
        scores['three'] *= 0.78
        scores['four'] *= 0.74
    if s['pinky'] < 0.68:
        scores['four'] *= 0.78
    if s['index'] < 0.66:
        scores['one'] *= 0.78

    if max(s[name] for name in ('index', 'middle', 'ring', 'pinky')) < 0.60 and s['thumb'] < 0.68:
        fist_boost = clamp01((0.64 - non_thumb_open) / 0.18)
        scores['fist'] = max(scores['fist'], (0.48 + 0.28 * fist_boost) * (0.30 + 0.70 * fist_fold_profile))
    if compact_hook_fist_gate > 0.34 and folded_thumb_gate > 0.18:
        scores['fist'] = clamp01(max(scores['fist'], 0.62 + 0.24 * compact_hook_fist_gate))
    if min(s[name] for name in ('index', 'middle', 'ring', 'pinky')) > 0.92 and s['thumb'] < 0.68:
        scores['four'] = clamp01(scores['four'] + 0.10)

    zero_pinch = clamp01((0.34 - pinch['thumb_index']) / 0.16)
    zero_tail_closed_gate = clamp01((tail_closed - 0.52) / 0.20)
    zero_index_gate = clamp01((0.58 - s['index']) / 0.26)
    zero_thumb_gate = clamp01((s['thumb'] - 0.60) / 0.18)
    zero_any_close = clamp01((0.60 - features.thumb_index_any_ratio) / 0.26)
    zero_loop = features.thumb_index_loop_score
    zero_loop_core = features.thumb_index_loop_core
    zero_shape_gate = avg((zero_tail_closed_gate, zero_index_gate, zero_thumb_gate))
    scores['zero'] *= 0.14 + 0.86 * avg((zero_loop, zero_shape_gate))
    if zero_thumb_gate > 0.44 and zero_index_gate > 0.38 and zero_tail_closed_gate > 0.34 and zero_loop > 0.28:
        scores['zero'] = clamp01(scores['zero'] + 0.16 * zero_thumb_gate + 0.14 * zero_index_gate + 0.12 * zero_loop + 0.08 * zero_any_close)
    if zero_loop_core > 0.34 and zero_tail_closed_gate > 0.32:
        scores['zero'] = clamp01(scores['zero'] + 0.18 * zero_loop_core + 0.08 * zero_tail_closed_gate)
    if zero_loop_core > 0.52 and s['index'] < 0.34:
        scores['zero'] = clamp01(max(scores['zero'], 0.76 + 0.18 * zero_loop_core))
    elif zero_loop < 0.28 or (zero_loop_core < 0.18 and features.thumb_index_tip_ratio > 0.24):
        scores['zero'] *= 0.16

    if pinky_shape_open < 0.56:
        scores['mute'] *= 0.42
    if zero_loop > 0.42:
        scores['mute'] *= 0.22
    scores['mute'] *= 0.18 + 0.82 * mute_rest_fold_gate
    scores['mute'] *= 1.0 - 0.92 * clamp01((open_rest_gate - 0.42) / 0.22)
    if min(s['thumb'], s['index'], s['middle'], s['ring'], s['pinky']) > 0.62:
        scores['mute'] *= 0.04

    fist_compact_gate = clamp01((0.64 - tail_open) / 0.24)
    scores['fist'] *= 0.28 + 0.72 * fist_compact_gate
    scores['fist'] *= 0.22 + 0.78 * fist_fold_profile
    scores['fist'] *= 0.40 + 0.60 * fist_retraction
    scores['fist'] *= 0.58 + 0.42 * clamp01((0.50 - zero_loop) / 0.28)
    scores['fist'] *= 0.60 + 0.40 * clamp01((0.60 - s['thumb']) / 0.30)
    scores['fist'] *= 1.0 - 0.28 * false_claw_gate
    scores['fist'] *= 1.0 - 0.84 * curled_shell_penalty
    if tail_closed > 0.68 and compact_hook_fist_gate > 0.32 and (s['thumb'] < 0.58 or zero_loop < 0.22):
        scores['fist'] = clamp01(max(scores['fist'], 0.68 + 0.18 * compact_hook_fist_gate))
    if zero_thumb_gate > 0.44 and zero_index_gate > 0.34 and zero_tail_closed_gate > 0.30 and zero_loop > 0.30:
        scores['fist'] *= 0.42
    if max(s[name] for name in ('index', 'middle', 'ring', 'pinky')) < 0.22 and fist_fold_profile > 0.42 and zero_loop < 0.28:
        scores['fist'] = clamp01(max(scores['fist'], 0.82))
        scores['zero'] *= 0.10

    two_apart_gate = clamp01((spread['index_middle'] - 0.42) / 0.14)
    two_together_penalty = clamp01((0.38 - spread['index_middle']) / 0.14)
    scores['two'] *= 0.24 + 0.76 * two_apart_gate
    scores['two'] *= 1.0 - 0.82 * two_together_penalty
    two_pair_open = avg((max(s['index'], ext['index']), max(s['middle'], ext['middle'])))
    two_tail_fold = avg(
        max(clamp01((curl[name] - 0.42) / 0.24), clamp01((0.56 - s[name]) / 0.24), 0.68 * hook[name])
        for name in ('ring', 'pinky')
    )
    if (
        two_pair_open > 0.74
        and two_tail_fold > 0.54
        and max(s['ring'], ext['ring']) < 0.46
        and max(s['pinky'], ext['pinky']) < 0.46
        and spread['index_middle'] >= 0.49
    ):
        scores['two'] = clamp01(max(scores['two'], 0.72 + 0.18 * avg((two_pair_open, two_tail_fold, clamp01((spread['index_middle'] - 0.49) / 0.05)))))
        scores['three'] *= 0.34

    volume_pair_open = avg((max(s['index'], ext['index']), max(s['middle'], ext['middle'])))
    scores['volume_pose'] *= 0.26 + 0.74 * volume_tail_fold_gate
    scores['volume_pose'] *= 1.0 - 0.70 * clamp01((spread['index_middle'] - 0.42) / 0.14)
    if features.spread_states.get('index_middle') != 'together':
        scores['volume_pose'] *= 0.08
    volume_fold_ok = avg(
        (
            max(clamp01((curl['thumb'] - 0.34) / 0.24), clamp01((0.66 - s['thumb']) / 0.22)),
            max(clamp01((curl['ring'] - 0.38) / 0.24), clamp01((0.62 - s['ring']) / 0.22), 0.72 * hook['ring']),
            max(clamp01((curl['pinky'] - 0.38) / 0.24), clamp01((0.62 - s['pinky']) / 0.22), 0.72 * hook['pinky']),
        )
    )
    volume_pose_core = clamp01((scores['volume_pose'] - 0.48) / 0.28)
    if (
        volume_pose_core > 0.0
        and volume_pair_open > 0.70
        and features.states['index'] == 'open'
        and features.states['middle'] == 'open'
        and features.states['ring'] != 'open'
        and features.states['pinky'] != 'open'
        and features.states['thumb'] != 'open'
        and features.spread_states.get('index_middle') == 'together'
    ):
        scores['volume_pose'] = clamp01(max(scores['volume_pose'], 0.62 + 0.24 * avg((volume_pose_core, volume_pair_open, volume_fold_ok))))
        scores['two'] *= 0.20 + 0.80 * two_apart_gate
        scores['three'] *= 0.28 + 0.72 * clamp01((spread['middle_ring'] - 0.42) / 0.16)
    else:
        scores['volume_pose'] *= 0.22

    mute_core = clamp01((scores['mute'] - 0.40) / 0.30)
    if mute_core > 0.0:
        scores['mute'] = clamp01(max(scores['mute'], 0.60 + 0.26 * mute_core))
        scores['volume_pose'] *= 0.26 + 0.74 * clamp01((0.34 - max(s['thumb'], s['pinky'])) / 0.22)

    three_shape = avg((s['index'], s['middle'], s['ring'], 1.0 - s['pinky'], 1.0 - s['thumb']))
    if s['index'] > 0.68 and s['middle'] > 0.68 and s['ring'] > 0.64:
        scores['three'] = clamp01(scores['three'] + 0.12 * three_shape)
    if s['pinky'] > 0.56:
        scores['three'] *= 0.72
    if s['thumb'] > 0.62:
        scores['three'] *= 0.82

    return scores



def _rank(scores: Dict[str, float]) -> Tuple[Tuple[str, float], Tuple[str, float]]:
    ranked = sorted(((label, scores.get(label, 0.0)) for label in ACTUAL_GESTURE_LABELS), key=lambda kv: kv[1], reverse=True)
    best = ranked[0] if ranked else ('neutral', 0.0)
    second = ranked[1] if len(ranked) > 1 else ('neutral', 0.0)
    return best, second



def classify_static(features: StaticFeatures) -> GesturePrediction:
    scores = score_static_candidates(features)
    s = features.open_scores
    ext = features.extension_scores
    curl = features.curl_scores
    hook = features.hook_scores
    spread = features.spread_ratios

    tail_closed = avg(1.0 - s[name] for name in ('middle', 'ring', 'pinky'))
    four_non_thumb = min(s[name] for name in ('index', 'middle', 'ring', 'pinky'))
    compact_cluster = avg(clamp01((0.44 - spread[key]) / 0.18) for key in ('index_middle', 'middle_ring', 'ring_pinky'))
    curled_non_thumb = avg(max(curl[name], 0.76 * hook[name]) for name in ('index', 'middle', 'ring', 'pinky'))
    fist_fold_profile = avg(
        clamp01((features.bend_angles[name][0] - 148.0) / 24.0)
        * clamp01((116.0 - features.bend_angles[name][1]) / 36.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    claw_hook_profile = avg(hook[name] for name in ('index', 'middle', 'ring', 'pinky'))
    claw_shape_profile = avg(
        clamp01((features.bend_angles[name][0] - 154.0) / 18.0)
        * clamp01((132.0 - features.bend_angles[name][1]) / 52.0)
        * clamp01((features.wrist_tip_ratios[name] - features.wrist_pip_ratios[name] - 0.04) / 0.18)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    claw_profile = max(claw_hook_profile, claw_shape_profile)
    fist_retraction = avg(
        clamp01((0.05 - (features.wrist_tip_ratios[name] - features.wrist_pip_ratios[name])) / 0.14)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    curled_shell_penalty = avg(
        clamp01((100.0 - features.bend_angles[name][0]) / 40.0)
        * clamp01((features.bend_angles[name][1] - 104.0) / 24.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    compact_hook_fist_gate = clamp01((compact_cluster + fist_retraction + clamp01((curled_non_thumb - 0.54) / 0.30) - 1.05) / 0.48)
    false_claw_gate = (
        clamp01((claw_profile - 0.34) / 0.28)
        * clamp01((avg(ext[name] for name in ('index', 'middle', 'ring', 'pinky')) - 0.40) / 0.24)
        * clamp01((0.34 - compact_cluster) / 0.22)
    )
    mute_rest_fold_gate = avg(
        max(clamp01((0.64 - s[name]) / 0.24), clamp01((curl[name] - 0.40) / 0.22), 0.72 * hook[name])
        for name in ('index', 'middle', 'ring')
    )

    if features.states['thumb'] == 'open' and four_non_thumb > 0.78:
        scores['open_hand'] = clamp01(max(scores['open_hand'], 0.82 + 0.14 * clamp01((s['thumb'] - 0.60) / 0.20)))
        scores['four'] *= 0.12

    if s['thumb'] < 0.56 and four_non_thumb > 0.74:
        scores['four'] = clamp01(max(scores['four'], 0.78 + 0.16 * clamp01((0.56 - s['thumb']) / 0.18)))
    elif features.states['thumb'] == 'unknown' and s['thumb'] < 0.62 and four_non_thumb > 0.82:
        scores['four'] = clamp01(max(scores['four'], 0.70 + 0.10 * clamp01((0.62 - s['thumb']) / 0.10)))

    if s['thumb'] > 0.66 and s['index'] < 0.36 and features.thumb_index_loop_core > 0.28:
        scores['zero'] = clamp01(max(scores['zero'], 0.74 + 0.16 * avg((s['thumb'], 1.0 - s['index'], max(tail_closed, 0.52), features.thumb_index_loop_core))))
        scores['fist'] *= 0.24
        scores['mute'] *= 0.26

    zero_side_gate = clamp01((0.76 - features.spread_ratios['thumb_index']) / 0.24)
    zero_any_gate = clamp01((0.78 - features.thumb_index_any_ratio) / 0.30)
    if s['thumb'] > 0.60 and s['index'] < 0.30 and tail_closed > 0.74 and (zero_side_gate > 0.30 or zero_any_gate > 0.34):
        zero_override = avg((clamp01((s['thumb'] - 0.60) / 0.18), clamp01((0.34 - s['index']) / 0.20), clamp01((tail_closed - 0.74) / 0.18), max(zero_side_gate, zero_any_gate)))
        scores['zero'] = clamp01(max(scores['zero'], 0.78 + 0.18 * zero_override))
        scores['fist'] *= 0.28
        scores['one'] *= 0.40

    if compact_hook_fist_gate > 0.34 and (s['thumb'] < 0.62 or curl['thumb'] > 0.34):
        scores['fist'] = clamp01(max(scores['fist'], 0.70 + 0.18 * compact_hook_fist_gate))

    if avg(s[name] for name in ('index', 'middle', 'ring', 'pinky')) < 0.50 and max(s[name] for name in ('index', 'middle', 'ring', 'pinky')) < 0.58 and fist_fold_profile > 0.34 and features.thumb_index_loop_score < 0.18 and (s['thumb'] < 0.72 or curl['thumb'] > 0.30):
        compact_gate = avg((clamp01((0.58 - s['index']) / 0.20), clamp01((0.58 - s['middle']) / 0.20), clamp01((0.58 - s['ring']) / 0.20), clamp01((0.58 - s['pinky']) / 0.20)))
        scores['fist'] = clamp01(max(scores['fist'], 0.72 + 0.16 * max(compact_gate, compact_hook_fist_gate)))
        scores['zero'] *= 0.10

    if s['thumb'] > 0.62 and s['pinky'] > 0.50 and mute_rest_fold_gate > 0.62 and max(s[name] for name in ('index', 'middle', 'ring')) < 0.42:
        scores['mute'] = clamp01(max(scores['mute'], 0.80 + 0.12 * avg((s['thumb'], s['pinky']))))
        scores['volume_pose'] *= 0.20
        scores['zero'] *= 0.34

    if false_claw_gate > 0.36:
        scores['fist'] *= 1.0 - 0.36 * false_claw_gate
    if curled_shell_penalty > 0.24:
        scores['fist'] *= 1.0 - 0.72 * curled_shell_penalty

    open_palm_gate = clamp01((min(s['thumb'], s['index'], s['middle'], s['ring'], s['pinky']) - 0.62) / 0.12)
    if open_palm_gate > 0.0:
        scores['mute'] *= 0.02 + 0.10 * (1.0 - open_palm_gate)
        scores['volume_pose'] *= 0.08 + 0.18 * (1.0 - open_palm_gate)

    best, second = _rank(scores)
    best_label, best_score = best
    second_score = second[1]
    margin = best_score - second_score
    confidence = clamp01(max(best_score, 0.72 * best_score + 0.28 * max(0.0, margin + 0.08)))

    if best_label == 'fist' and (
        false_claw_gate > 0.54 and compact_hook_fist_gate < 0.22
        or curled_shell_penalty > 0.34
    ):
        return GesturePrediction('neutral', confidence * 0.72, scores)

    min_score = 0.52 if best_label == 'volume_pose' else 0.56 if best_label == 'mute' else 0.58
    if best_score < min_score or margin < 0.030:
        return GesturePrediction('neutral', confidence * 0.65, scores)
    return GesturePrediction(best_label, confidence, scores)
