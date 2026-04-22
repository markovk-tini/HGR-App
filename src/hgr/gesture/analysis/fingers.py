from __future__ import annotations

from typing import Dict

import numpy as np

from ..models import FingerReading
from .geometry import angle_deg, clamp01, distance, normalize_range
from .occlusion import estimate_finger_occlusion


FINGER_CHAINS: dict[str, tuple[int, int, int, int]] = {
    "thumb": (1, 2, 3, 4),
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}


def _non_thumb_state_from_metrics(
    openness: float,
    hook_score: float,
    bend_base: float,
    bend_proximal: float,
    bend_distal: float,
    reach: float,
    palm_distance: float,
    curl: float,
) -> str:
    # A true open finger keeps both joints extended and maintains meaningful reach.
    if openness >= 0.76 and bend_distal >= 148.0 and reach >= 0.22:
        return "fully_open"
    # Compact fists should resolve as closed, while curved shells stay curled.
    compact_fist = (
        curl >= 0.78
        and openness <= 0.28
        and bend_base <= 136.0
        and bend_proximal >= 140.0
        and bend_distal <= 108.0
        and reach <= 0.12
        and palm_distance <= 0.60
    )
    if compact_fist:
        return "closed"

    if curl >= 0.82 and openness <= 0.26 and bend_base <= 140.0 and bend_distal <= 108.0 and reach <= 0.08:
        return "closed"

    if bend_distal <= 98.0 and bend_base <= 138.0 and reach <= 0.08 and palm_distance <= 1.22 and openness <= 0.34:
        return "closed"

    shell_like = (
        openness <= 0.56
        and bend_base >= 144.0
        and bend_proximal <= 130.0
        and bend_distal <= 136.0
        and reach <= 0.20
        and palm_distance <= 0.64
    )
    if shell_like:
        return "mostly_curled"

    if curl >= 0.80 and openness <= 0.34:
        if bend_base >= 148.0 and palm_distance >= 0.74:
            return "mostly_curled"
        return "closed"
    # Hooked fingers and looser curls stay in the curled bands, not closed.
    if hook_score >= 0.42 and reach >= 0.05 and openness >= 0.28:
        return "partially_curled"

    if bend_distal <= 122.0 and bend_base <= 140.0 and reach <= 0.10 and palm_distance <= 0.98 and curl >= 0.72 and openness <= 0.42:
        return "closed"
    if bend_base >= 150.0 and bend_distal <= 138.0 and palm_distance >= 0.72:
        return "mostly_curled"
    if bend_distal <= 134.0 and reach <= 0.18 and palm_distance <= 1.28 and openness <= 0.68:
        return "mostly_curled"
    if openness >= 0.54 or hook_score >= 0.30:
        return "partially_curled"
    if openness >= 0.34 or bend_distal >= 100.0:
        return "mostly_curled"
    return "closed"


def _thumb_state_from_metrics(
    openness: float,
    bend_proximal: float,
    bend_distal: float,
    tip_palm: float,
    reach: float,
    lateral_reach: float,
    thumb_index_gap: float,
) -> str:
    if openness >= 0.48 and bend_distal >= 134.0 and (
        tip_palm >= 0.50 or lateral_reach >= 1.02 or thumb_index_gap >= 0.42
    ):
        return "fully_open"
    if openness <= 0.26 and tip_palm <= 0.42 and reach <= 0.36:
        return "closed"
    if openness <= 0.40 and bend_distal <= 150.0 and tip_palm <= 0.66 and thumb_index_gap <= 0.58:
        return "mostly_curled"
    if openness >= 0.34 and bend_distal >= 122.0 and tip_palm >= 0.46:
        return "mostly_curled"
    return "closed"


def _confidence_from_state(state: str, openness: float, occlusion_score: float) -> float:
    boundaries = {
        "fully_open": abs(openness - 0.82),
        "partially_curled": min(abs(openness - 0.54), abs(openness - 0.70)),
        "mostly_curled": min(abs(openness - 0.26), abs(openness - 0.48)),
        "closed": abs(openness - 0.18),
    }
    confidence = clamp01(0.42 + 1.35 * boundaries.get(state, 0.0))
    return clamp01(confidence * (1.0 - 0.45 * occlusion_score))


def _thumb_reading(name: str, landmarks: np.ndarray, palm_center: np.ndarray, palm_scale: float) -> FingerReading:
    mcp, pip, dip, tip = FINGER_CHAINS[name]
    bend_proximal = angle_deg(landmarks[mcp], landmarks[pip], landmarks[dip])
    bend_distal = angle_deg(landmarks[pip], landmarks[dip], landmarks[tip])
    tip_palm = distance(landmarks[tip], palm_center) / palm_scale
    pip_palm = distance(landmarks[pip], palm_center) / palm_scale
    reach = (distance(landmarks[tip], landmarks[0]) - distance(landmarks[pip], landmarks[0])) / palm_scale
    lateral_reach = max(
        distance(landmarks[tip], landmarks[5]) / palm_scale,
        distance(landmarks[tip], landmarks[17]) / palm_scale,
    )
    thumb_index_gap = distance(landmarks[tip], landmarks[5]) / palm_scale
    openness = (
        0.22 * normalize_range(bend_proximal, 110.0, 178.0)
        + 0.20 * normalize_range(bend_distal, 105.0, 178.0)
        + 0.20 * normalize_range(tip_palm - pip_palm, 0.00, 0.62)
        + 0.08 * normalize_range(reach, -0.02, 0.58)
        + 0.16 * normalize_range(lateral_reach, 0.28, 1.35)
        + 0.14 * normalize_range(thumb_index_gap, 0.28, 1.10)
    )
    if thumb_index_gap <= 0.48 and tip_palm <= 0.70:
        openness -= 0.18 * clamp01((0.48 - thumb_index_gap) / 0.14)
    openness = clamp01(openness)
    hook_score = clamp01((bend_proximal - 145.0) / 24.0) * clamp01((132.0 - bend_distal) / 42.0)
    state = _thumb_state_from_metrics(
        openness,
        bend_proximal,
        bend_distal,
        tip_palm,
        reach,
        lateral_reach,
        thumb_index_gap,
    )
    curl = clamp01(1.0 - openness + 0.20 * normalize_range(150.0 - bend_distal, 0.0, 70.0))
    confidence = _confidence_from_state(state, openness, 0.0)
    return FingerReading(
        name=name,
        state=state,
        openness=float(openness),
        curl=float(curl),
        confidence=float(confidence),
        occluded=False,
        bend_base=0.0,
        bend_proximal=float(bend_proximal),
        bend_distal=float(bend_distal),
        palm_distance=float(tip_palm),
        reach=float(reach),
    )


def analyze_fingers(landmarks: np.ndarray, palm_center: np.ndarray, palm_scale: float) -> Dict[str, FingerReading]:
    readings: Dict[str, FingerReading] = {}
    finger_order = list(FINGER_CHAINS.keys())

    for index, (name, chain) in enumerate(FINGER_CHAINS.items()):
        if name == "thumb":
            readings[name] = _thumb_reading(name, landmarks, palm_center, palm_scale)
            continue

        mcp, pip, dip, tip = chain
        bend_base = angle_deg(landmarks[0], landmarks[mcp], landmarks[pip])
        bend_proximal = angle_deg(landmarks[mcp], landmarks[pip], landmarks[dip])
        bend_distal = angle_deg(landmarks[pip], landmarks[dip], landmarks[tip])
        tip_palm = distance(landmarks[tip], palm_center) / palm_scale
        pip_palm = distance(landmarks[pip], palm_center) / palm_scale
        reach = (distance(landmarks[tip], landmarks[0]) - distance(landmarks[pip], landmarks[0])) / palm_scale
        span = (distance(landmarks[mcp], landmarks[tip]) - distance(landmarks[mcp], landmarks[pip])) / palm_scale
        extension_gap = tip_palm - pip_palm
        neighboring_tips = []
        if index > 1:
            neighboring_tips.append(landmarks[FINGER_CHAINS[finger_order[index - 1]][3]])
        if index + 1 < len(finger_order):
            neighboring_tips.append(landmarks[FINGER_CHAINS[finger_order[index + 1]][3]])
        occlusion_score = estimate_finger_occlusion(landmarks, chain, palm_scale, neighboring_tips)
        hook_score = (
            clamp01((bend_proximal - 150.0) / 22.0)
            * clamp01((138.0 - bend_distal) / 50.0)
            * clamp01((reach - 0.02) / 0.18)
        )
        distal_open = normalize_range(bend_distal, 112.0, 178.0)
        proximal_open = normalize_range(bend_proximal, 120.0, 178.0)
        proximal_support = proximal_open * (0.28 + 0.72 * distal_open)
        openness = (
            0.24 * proximal_support
            + 0.20 * distal_open
            + 0.22 * normalize_range(extension_gap, -0.02, 0.60)
            + 0.18 * normalize_range(reach, -0.04, 0.72)
            + 0.12 * normalize_range(span, 0.16, 1.12)
        )
        openness = max(openness, 0.14 + 0.26 * hook_score)
        curl = clamp01(1.0 - openness + 0.16 * normalize_range(145.0 - bend_distal, 0.0, 75.0))
        state = _non_thumb_state_from_metrics(
            openness,
            hook_score,
            bend_base,
            bend_proximal,
            bend_distal,
            reach,
            tip_palm,
            curl,
        )
        if (
            name == "pinky"
            and state != "fully_open"
            and openness >= 0.52
            and bend_base >= 96.0
            and bend_proximal >= 150.0
            and bend_distal >= 154.0
            and tip_palm >= 0.90
            and reach >= 0.12
        ):
            state = "fully_open"
        confidence = _confidence_from_state(state, openness, occlusion_score)
        readings[name] = FingerReading(
            name=name,
            state=state,
            openness=float(openness),
            curl=float(curl),
            confidence=float(confidence),
            occluded=occlusion_score >= 0.52,
            bend_base=float(bend_base),
            bend_proximal=float(bend_proximal),
            bend_distal=float(bend_distal),
            palm_distance=float(tip_palm),
            reach=float(reach),
        )

    return readings
