from __future__ import annotations

import numpy as np

from .gesture_types import DynamicDetectionContext, avg, clamp01


def score_swipe_left(context: DynamicDetectionContext) -> dict[str, float]:
    if context.state.sample_count < 4 or not context.state.has_anchor:
        return {}

    s = context.features.open_scores
    primary_open_gate = clamp01((min(s['index'], s['middle']) - 0.56) / 0.18)
    support_open_gate = clamp01((max(s['ring'], s['pinky']) - 0.40) / 0.24)
    pose_gate = primary_open_gate * (0.55 + 0.45 * support_open_gate)
    if pose_gate <= 0.0:
        return {}

    history = list(context.history)[-8:]
    deltas_x = [
        frame.motion_from_previous[0] / max(frame.palm_scale, 1e-6)
        for frame in history
        if frame.motion_from_previous is not None
    ]
    deltas_y = [
        abs(frame.motion_from_previous[1] / max(frame.palm_scale, 1e-6))
        for frame in history
        if frame.motion_from_previous is not None
    ]
    deltas_z = [
        abs(frame.motion_from_previous[2] / max(frame.palm_scale, 1e-6))
        for frame in history
        if frame.motion_from_previous is not None
    ]
    left_consistency = 0.0
    directional_streak = 0.0
    if deltas_x:
        left_consistency = sum(max(0.0, -dx) for dx in deltas_x) / max(1e-6, sum(abs(dx) for dx in deltas_x))
        forward_frames = sum(1 for dx in deltas_x if dx < -0.035)
        reverse_frames = sum(1 for dx in deltas_x if dx > 0.02)
        directional_streak = clamp01((forward_frames - reverse_frames - 1.0) / 2.5)

    horizontal = 0.0
    vertical = 0.0
    depth = 0.0
    path_length = 0.0
    duration = max(history[-1].timestamp - history[0].timestamp, 1e-6) if len(history) >= 2 else 1e-6
    peak_speed = 0.0
    if len(history) >= 2 and history[0].centroid is not None and history[-1].centroid is not None:
        recent_displacement = (history[-1].centroid - history[0].centroid) / max(history[-1].palm_scale, 1e-6)
        horizontal = max(0.0, -float(recent_displacement[0]))
        vertical = abs(float(recent_displacement[1]))
        depth = abs(float(recent_displacement[2]))
    for prev_frame, frame in zip(history, history[1:]):
        if frame.motion_from_previous is None:
            continue
        scale = max(frame.palm_scale, 1e-6)
        step = frame.motion_from_previous / scale
        path_length += float(np.linalg.norm(step))
        dt = max(frame.timestamp - prev_frame.timestamp, 1e-6)
        peak_speed = max(peak_speed, max(0.0, -float(step[0])) / dt)
    lateral_noise = avg(deltas_y) + 0.70 * avg(deltas_z)
    speed_gate = clamp01((peak_speed - 1.55) / 1.15)
    path_gate = clamp01((path_length - 0.78) / 0.50)
    progress_gate = clamp01((horizontal - 0.54) / 0.28)
    dominance_gate = clamp01((horizontal - 0.95 * vertical - 0.80 * depth - 0.08) / 0.30)
    straightness_gate = clamp01(((horizontal / max(path_length, 1e-6)) - 0.48) / 0.24)
    noise_gate = clamp01((0.22 - lateral_noise) / 0.18)
    axis_ratio_gate = clamp01(((horizontal / max(vertical + 0.60 * depth, 1e-6)) - 1.35) / 0.50)
    duration_gate = clamp01((0.55 - duration) / 0.22)
    score = clamp01(
        (
            0.24 * progress_gate
            + 0.18 * path_gate
            + 0.16 * left_consistency
            + 0.16 * straightness_gate
            + 0.10 * dominance_gate
            + 0.08 * speed_gate
            + 0.08 * duration_gate
            + 0.08 * noise_gate
        )
        * (0.38 + 0.62 * pose_gate)
        * (0.50 + 0.50 * directional_streak)
        * (0.45 + 0.55 * axis_ratio_gate)
    )
    return {'swipe_left': score}
