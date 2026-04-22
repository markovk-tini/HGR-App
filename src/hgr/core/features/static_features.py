from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

from .geometry import angle_deg, distance, normalize_range

FINGER_DEF = {
    'index': (5, 6, 7, 8),
    'middle': (9, 10, 11, 12),
    'ring': (13, 14, 15, 16),
    'pinky': (17, 18, 19, 20),
}


@dataclass(frozen=True)
class StaticFeatures:
    palm_center: np.ndarray
    palm_scale: float
    open_scores: Dict[str, float]
    extension_scores: Dict[str, float]
    curl_scores: Dict[str, float]
    hook_scores: Dict[str, float]
    states: Dict[str, str]
    finger_state_biases: Dict[str, float]
    finger_state_confidences: Dict[str, float]
    bend_angles: Dict[str, Tuple[float, float]]
    tip_palm_ratios: Dict[str, float]
    pip_palm_ratios: Dict[str, float]
    wrist_tip_ratios: Dict[str, float]
    wrist_pip_ratios: Dict[str, float]
    finger_count_open: int
    spread_ratios: Dict[str, float]
    spread_states: Dict[str, str]
    spread_together_strengths: Dict[str, float]
    spread_apart_strengths: Dict[str, float]
    pinch_ratios: Dict[str, float]
    thumb_index_tip_ratio: float
    thumb_index_pip_ratio: float
    thumb_index_mcp_ratio: float
    thumb_index_any_ratio: float
    thumb_index_loop_core: float
    thumb_index_loop_score: float


def _state_from_score(score: float) -> str:
    if score >= 0.64:
        return 'open'
    if score <= 0.36:
        return 'closed'
    return 'unknown'


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _adjacent_pair_ratio(landmarks: np.ndarray, a: tuple[int, int, int], b: tuple[int, int, int], palm_scale: float) -> float:
    scale = max(palm_scale, 1e-6)
    tip = distance(landmarks[a[0]], landmarks[b[0]])
    dip = distance(landmarks[a[1]], landmarks[b[1]])
    pip = distance(landmarks[a[2]], landmarks[b[2]])
    skew = min(
        distance(landmarks[a[0]], landmarks[b[1]]),
        distance(landmarks[a[1]], landmarks[b[0]]),
    )
    # Using the minimum alone makes obviously separated fingers look "together"
    # whenever the bases stay close. A weighted blend tracks visible separation
    # more faithfully while still tolerating slight rotations.
    blended = 0.52 * tip + 0.24 * dip + 0.14 * pip + 0.10 * skew
    return float(blended / scale)


def _resolve_finger_state(
    score: float,
    *,
    finger_name: str,
    tip_palm: float,
    pip_palm: float,
    wrist_tip: float,
    wrist_pip: float,
    bend_pair: tuple[float, float],
    mcp_tip: float,
    mcp_pip: float,
    extension_score: float,
    curl_score: float,
    hook_score: float,
) -> str:
    extension_gap = tip_palm - pip_palm
    wrist_gap = wrist_tip - wrist_pip
    is_outer_finger = finger_name in {'ring', 'pinky'}
    is_thumb = finger_name == 'thumb'
    span_gap = mcp_tip - mcp_pip

    if not is_thumb:
        very_open = (
            extension_score >= (0.70 if is_outer_finger else 0.74)
            or (
                min(bend_pair) >= (148.0 if is_outer_finger else 154.0)
                and extension_gap >= (0.14 if is_outer_finger else 0.17)
                and wrist_gap >= (0.10 if is_outer_finger else 0.14)
                and span_gap >= (0.72 if is_outer_finger else 0.82)
            )
        )
        very_closed = (
            curl_score >= (0.74 if is_outer_finger else 0.78)
            and hook_score <= 0.22
            and extension_gap <= (0.08 if is_outer_finger else 0.06)
            and wrist_gap <= (0.04 if is_outer_finger else 0.03)
            and span_gap <= (0.72 if is_outer_finger else 0.78)
        )
        hook_like = (
            hook_score >= (0.30 if is_outer_finger else 0.34)
            and extension_score >= 0.26
            and curl_score <= 0.90
        )
        soft_closed = (
            extension_score <= (0.44 if is_outer_finger else 0.34)
            and span_gap <= (1.04 if is_outer_finger else 0.90)
            and (
                curl_score >= (0.42 if is_outer_finger else 0.48)
                or (extension_gap <= (0.14 if is_outer_finger else 0.12) and wrist_gap <= (0.08 if is_outer_finger else 0.06))
            )
        )
        distal_folded_closed = (
            hook_score <= (0.12 if is_outer_finger else 0.08)
            and bend_pair[1] <= (98.0 if is_outer_finger else 92.0)
            and wrist_gap <= (0.04 if is_outer_finger else 0.02)
            and extension_score <= (0.46 if is_outer_finger else 0.40)
        )
        compact_hook_closed = (
            hook_like
            and extension_score <= (0.34 if is_outer_finger else 0.30)
            and wrist_gap <= (0.10 if is_outer_finger else 0.08)
            and span_gap <= (1.00 if is_outer_finger else 0.94)
        )
        if very_open:
            return 'open'
        if hook_like and wrist_gap >= (0.12 if is_outer_finger else 0.14):
            return 'unknown'
        if very_closed or soft_closed or compact_hook_closed or distal_folded_closed:
            return 'closed'
        if hook_like:
            return 'unknown'
        if extension_score - curl_score >= 0.18 and score >= 0.48:
            return 'open'
        if curl_score - extension_score >= 0.18 and score <= 0.50:
            return 'closed'
        return 'unknown'

    thumb_open = extension_score >= 0.68 or score >= 0.70
    thumb_closed = curl_score >= 0.72 and extension_score <= 0.40 and span_gap <= 0.92
    if thumb_open:
        return 'open'
    if thumb_closed:
        return 'closed'
    if extension_score - curl_score >= 0.12 and score >= 0.52:
        return 'open'
    if curl_score - extension_score >= 0.18 and score <= 0.46:
        return 'closed'
    return 'unknown'


def extract_static_features(landmarks: np.ndarray) -> StaticFeatures:
    palm_center = np.mean(landmarks[[0, 5, 9, 13, 17]], axis=0)
    palm_width = max(distance(landmarks[5], landmarks[17]), 1e-6)
    palm_height = max(distance(landmarks[0], landmarks[9]), 1e-6)
    palm_scale = max((palm_width + palm_height) * 0.5, 1e-6)

    open_scores: Dict[str, float] = {}
    extension_scores: Dict[str, float] = {}
    curl_scores: Dict[str, float] = {}
    hook_scores: Dict[str, float] = {}
    states: Dict[str, str] = {}
    finger_state_biases: Dict[str, float] = {}
    finger_state_confidences: Dict[str, float] = {}
    bend_angles: Dict[str, Tuple[float, float]] = {}
    tip_palm_ratios: Dict[str, float] = {}
    pip_palm_ratios: Dict[str, float] = {}
    wrist_tip_ratios: Dict[str, float] = {}
    wrist_pip_ratios: Dict[str, float] = {}

    for name, (mcp, pip, dip, tip) in FINGER_DEF.items():
        is_outer = name in {'ring', 'pinky'}
        a1 = angle_deg(landmarks[mcp], landmarks[pip], landmarks[dip])
        a2 = angle_deg(landmarks[pip], landmarks[dip], landmarks[tip])
        bend_angles[name] = (float(a1), float(a2))

        tip_palm = distance(landmarks[tip], palm_center) / palm_scale
        pip_palm = distance(landmarks[pip], palm_center) / palm_scale
        wrist_tip = distance(landmarks[tip], landmarks[0]) / palm_scale
        wrist_pip = distance(landmarks[pip], landmarks[0]) / palm_scale
        mcp_tip = distance(landmarks[mcp], landmarks[tip]) / palm_scale
        mcp_pip = distance(landmarks[mcp], landmarks[pip]) / palm_scale

        tip_palm_ratios[name] = float(tip_palm)
        pip_palm_ratios[name] = float(pip_palm)
        wrist_tip_ratios[name] = float(wrist_tip)
        wrist_pip_ratios[name] = float(wrist_pip)

        extension_gap = tip_palm - pip_palm
        wrist_gap = wrist_tip - wrist_pip
        span_gap = mcp_tip - mcp_pip

        proximal_open = normalize_range(a1, 132.0 if is_outer else 138.0, 176.0)
        distal_open = normalize_range(a2, 110.0 if is_outer else 118.0, 176.0)
        extension_core = 0.42 * proximal_open + 0.58 * distal_open
        reach_score = 0.58 * normalize_range(extension_gap, 0.00, 0.52 if is_outer else 0.56) + 0.42 * normalize_range(wrist_gap, 0.00, 0.56 if is_outer else 0.60)
        span_score = 0.58 * normalize_range(span_gap, 0.42 if is_outer else 0.48, 1.20 if is_outer else 1.34) + 0.42 * normalize_range(mcp_tip, 0.70 if is_outer else 0.80, 1.92 if is_outer else 2.12)
        extension_score = _clip01(0.40 * extension_core + 0.32 * reach_score + 0.28 * span_score)

        proximal_curl = normalize_range(172.0 - a1, 0.0, 72.0 if is_outer else 66.0)
        distal_curl = normalize_range(170.0 - a2, 0.0, 92.0 if is_outer else 86.0)
        compact_score = 0.42 * normalize_range((0.10 if is_outer else 0.08) - extension_gap, 0.0, 0.24) + 0.30 * normalize_range((0.05 if is_outer else 0.04) - wrist_gap, 0.0, 0.18) + 0.28 * normalize_range((0.82 if is_outer else 0.86) - span_gap, 0.0, 0.34)
        curl_score = _clip01(0.48 * proximal_curl + 0.52 * distal_curl)
        curl_score = _clip01(max(curl_score * 0.82 + compact_score * 0.40, compact_score * 0.86))

        hook_score = _clip01(
            normalize_range(a1, 146.0 if is_outer else 150.0, 176.0)
            * normalize_range((132.0 if is_outer else 124.0) - a2, 0.0, 54.0)
            * normalize_range(wrist_gap, 0.02, 0.24)
        )

        open_score = _clip01(0.74 * extension_score + 0.12 * normalize_range(tip_palm, 0.70, 1.90) + 0.14 * normalize_range(wrist_tip, 0.92, 2.08))
        if hook_score > 0.22:
            open_score = max(open_score, 0.22 + 0.34 * hook_score)
        if curl_score > 0.74 and hook_score < 0.24:
            open_score *= 0.76

        open_scores[name] = float(open_score)
        extension_scores[name] = float(extension_score)
        curl_scores[name] = float(curl_score)
        hook_scores[name] = float(hook_score)

        states[name] = _resolve_finger_state(
            open_score,
            finger_name=name,
            tip_palm=tip_palm,
            pip_palm=pip_palm,
            wrist_tip=wrist_tip,
            wrist_pip=wrist_pip,
            bend_pair=(float(a1), float(a2)),
            mcp_tip=float(mcp_tip),
            mcp_pip=float(mcp_pip),
            extension_score=float(extension_score),
            curl_score=float(curl_score),
            hook_score=float(hook_score),
        )
        bias = extension_score - curl_score
        finger_state_biases[name] = float(bias)
        confidence = max(abs(bias), abs(open_score - 0.50) * 0.90)
        if states[name] == 'open':
            confidence = max(confidence, normalize_range(extension_score, 0.54, 0.92))
        elif states[name] == 'closed':
            confidence = max(confidence, normalize_range(curl_score, 0.56, 0.92))
        elif hook_score > 0.26:
            confidence = max(0.24, min(0.72, 0.26 + 0.42 * hook_score))
        finger_state_confidences[name] = float(_clip01(confidence))

    a1 = angle_deg(landmarks[1], landmarks[2], landmarks[3])
    a2 = angle_deg(landmarks[2], landmarks[3], landmarks[4])
    bend_angles['thumb'] = (float(a1), float(a2))

    thumb_tip_palm = distance(landmarks[4], palm_center) / palm_scale
    thumb_pip_palm = float(distance(landmarks[2], palm_center) / palm_scale)
    thumb_wrist_tip = float(distance(landmarks[4], landmarks[0]) / palm_scale)
    thumb_wrist_pip = float(distance(landmarks[2], landmarks[0]) / palm_scale)
    thumb_mcp_tip = float(distance(landmarks[1], landmarks[4]) / palm_scale)
    thumb_mcp_pip = float(distance(landmarks[1], landmarks[2]) / palm_scale)
    thumb_index_gap = distance(landmarks[4], landmarks[5]) / palm_scale
    thumb_pinky_gap = distance(landmarks[4], landmarks[17]) / palm_scale

    tip_palm_ratios['thumb'] = float(thumb_tip_palm)
    pip_palm_ratios['thumb'] = thumb_pip_palm
    wrist_tip_ratios['thumb'] = thumb_wrist_tip
    wrist_pip_ratios['thumb'] = thumb_wrist_pip

    thumb_joint_open = 0.56 * normalize_range(a1, 108.0, 178.0) + 0.44 * normalize_range(a2, 112.0, 178.0)
    thumb_reach = 0.52 * normalize_range(thumb_tip_palm, 0.36, 1.16) + 0.28 * normalize_range(thumb_index_gap, 0.16, 0.94) + 0.20 * normalize_range(thumb_pinky_gap, 0.34, 1.48)
    thumb_extension = _clip01(0.56 * thumb_joint_open + 0.44 * thumb_reach)
    thumb_curl = _clip01(
        0.54 * normalize_range(172.0 - a1, 0.0, 74.0)
        + 0.46 * normalize_range(170.0 - a2, 0.0, 78.0)
    )
    thumb_compact = _clip01(
        0.50 * normalize_range(0.62 - thumb_tip_palm, 0.0, 0.34)
        + 0.50 * normalize_range(0.84 - (thumb_mcp_tip - thumb_mcp_pip), 0.0, 0.36)
    )
    thumb_curl = _clip01(max(thumb_curl * 0.82 + thumb_compact * 0.40, thumb_compact * 0.86))
    thumb_hook = 0.0
    thumb_open = _clip01(0.78 * thumb_extension + 0.22 * normalize_range(thumb_tip_palm, 0.44, 1.18))
    if thumb_curl > 0.74:
        thumb_open *= 0.78

    open_scores['thumb'] = float(thumb_open)
    extension_scores['thumb'] = float(thumb_extension)
    curl_scores['thumb'] = float(thumb_curl)
    hook_scores['thumb'] = float(thumb_hook)
    states['thumb'] = _resolve_finger_state(
        thumb_open,
        finger_name='thumb',
        tip_palm=thumb_tip_palm,
        pip_palm=thumb_pip_palm,
        wrist_tip=thumb_wrist_tip,
        wrist_pip=thumb_wrist_pip,
        bend_pair=(float(a1), float(a2)),
        mcp_tip=thumb_mcp_tip,
        mcp_pip=thumb_mcp_pip,
        extension_score=float(thumb_extension),
        curl_score=float(thumb_curl),
        hook_score=float(thumb_hook),
    )
    finger_state_biases['thumb'] = float(thumb_extension - thumb_curl)
    thumb_conf = max(abs(thumb_extension - thumb_curl), abs(thumb_open - 0.50) * 0.90)
    if states['thumb'] == 'open':
        thumb_conf = max(thumb_conf, normalize_range(thumb_extension, 0.54, 0.92))
    elif states['thumb'] == 'closed':
        thumb_conf = max(thumb_conf, normalize_range(thumb_curl, 0.56, 0.92))
    finger_state_confidences['thumb'] = float(_clip01(thumb_conf))

    thumb_index_adjacent = min(
        distance(landmarks[4], landmarks[5]),
        distance(landmarks[4], landmarks[6]),
        distance(landmarks[3], landmarks[5]),
    ) / palm_scale
    spread_ratios = {
        'thumb_index': float(thumb_index_adjacent),
        'index_middle': _adjacent_pair_ratio(landmarks, (8, 7, 6), (12, 11, 10), palm_scale),
        'middle_ring': _adjacent_pair_ratio(landmarks, (12, 11, 10), (16, 15, 14), palm_scale),
        'ring_pinky': _adjacent_pair_ratio(landmarks, (16, 15, 14), (20, 19, 18), palm_scale),
    }
    spread_thresholds = {
        'thumb_index': (0.40, 0.56),
        'index_middle': (0.48, 0.52),
        'middle_ring': (0.48, 0.56),
        'ring_pinky': (0.48, 0.52),
    }
    spread_states = {}
    spread_together_strengths: Dict[str, float] = {}
    spread_apart_strengths: Dict[str, float] = {}
    for key, value in spread_ratios.items():
        together_threshold, apart_threshold = spread_thresholds[key]
        if value <= together_threshold:
            spread_states[key] = 'together'
        elif value >= apart_threshold:
            spread_states[key] = 'apart'
        else:
            spread_states[key] = 'neutral'
        spread_range = max(apart_threshold - together_threshold, 1e-6)
        spread_together_strengths[key] = float(normalize_range(apart_threshold - value, 0.0, spread_range))
        spread_apart_strengths[key] = float(normalize_range(value - together_threshold, 0.0, spread_range))

    thumb_index_tip_ratio = distance(landmarks[4], landmarks[8]) / palm_scale
    thumb_index_pip_ratio = distance(landmarks[4], landmarks[6]) / palm_scale
    thumb_index_mcp_ratio = distance(landmarks[4], landmarks[5]) / palm_scale
    thumb_index_any_ratio = min(
        distance(landmarks[4], landmarks[5]),
        distance(landmarks[4], landmarks[6]),
        distance(landmarks[4], landmarks[7]),
        distance(landmarks[4], landmarks[8]),
    ) / palm_scale
    thumb_index_loop_core = (
        normalize_range(0.34 - thumb_index_tip_ratio, 0.0, 0.18)
        * normalize_range(thumb_index_pip_ratio, 0.62, 1.12)
        * normalize_range(thumb_index_mcp_ratio, 0.56, 1.08)
    )
    thumb_index_loop_score = max(
        thumb_index_loop_core,
        0.70 * thumb_index_loop_core
        + 0.20 * normalize_range(0.44 - thumb_index_tip_ratio, 0.0, 0.16)
        + 0.10 * normalize_range(thumb_tip_palm, 0.60, 1.04),
    )

    pinch_ratios = {
        'thumb_index': thumb_index_tip_ratio,
        'thumb_middle': distance(landmarks[4], landmarks[12]) / palm_scale,
        'thumb_ring': distance(landmarks[4], landmarks[16]) / palm_scale,
        'thumb_pinky': distance(landmarks[4], landmarks[20]) / palm_scale,
    }

    finger_count_open = sum(1 for name in ('thumb', 'index', 'middle', 'ring', 'pinky') if states[name] == 'open')

    return StaticFeatures(
        palm_center=palm_center,
        palm_scale=float(palm_scale),
        open_scores=open_scores,
        extension_scores=extension_scores,
        curl_scores=curl_scores,
        hook_scores=hook_scores,
        states=states,
        finger_state_biases=finger_state_biases,
        finger_state_confidences=finger_state_confidences,
        bend_angles=bend_angles,
        tip_palm_ratios=tip_palm_ratios,
        pip_palm_ratios=pip_palm_ratios,
        wrist_tip_ratios=wrist_tip_ratios,
        wrist_pip_ratios=wrist_pip_ratios,
        finger_count_open=finger_count_open,
        spread_ratios=spread_ratios,
        spread_states=spread_states,
        spread_together_strengths=spread_together_strengths,
        spread_apart_strengths=spread_apart_strengths,
        pinch_ratios=pinch_ratios,
        thumb_index_tip_ratio=float(thumb_index_tip_ratio),
        thumb_index_pip_ratio=float(thumb_index_pip_ratio),
        thumb_index_mcp_ratio=float(thumb_index_mcp_ratio),
        thumb_index_any_ratio=float(thumb_index_any_ratio),
        thumb_index_loop_core=float(thumb_index_loop_core),
        thumb_index_loop_score=float(thumb_index_loop_score),
    )
