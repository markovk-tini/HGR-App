from __future__ import annotations

from .gesture_types import StaticGestureScores, avg, clamp01, closed
from ..features.static_features import StaticFeatures



def score_group_a(features: StaticFeatures) -> StaticGestureScores:
    s = features.open_scores
    ext = features.extension_scores
    curl = features.curl_scores
    hook = features.hook_scores
    spread = features.spread_ratios
    pinch = features.pinch_ratios
    tips = features.tip_palm_ratios

    non_thumb_open = avg(s[name] for name in ('index', 'middle', 'ring', 'pinky'))
    non_thumb_ext = avg(ext[name] for name in ('index', 'middle', 'ring', 'pinky'))
    non_thumb_curl = avg(curl[name] for name in ('index', 'middle', 'ring', 'pinky'))
    hook_profile = avg(hook[name] for name in ('index', 'middle', 'ring', 'pinky'))
    curled_profile = avg(max(curl[name], 0.76 * hook[name]) for name in ('index', 'middle', 'ring', 'pinky'))
    all_open = avg(s.values())
    all_closed = avg(closed(score) for score in s.values())
    compact_tips = clamp01((1.16 - avg(tips.values())) / 0.72)
    thumb_open_bonus = clamp01((s['thumb'] - 0.72) / 0.20)
    relaxed_spread = avg(
        clamp01((spread[key] - base) / width)
        for key, base, width in (
            ('thumb_index', 0.32, 0.58),
            ('index_middle', 0.18, 0.38),
            ('middle_ring', 0.14, 0.34),
            ('ring_pinky', 0.12, 0.32),
        )
    )
    compact_cluster = avg(
        clamp01((0.44 - spread[key]) / 0.18)
        for key in ('index_middle', 'middle_ring', 'ring_pinky')
    )

    zero_pinch = clamp01((0.34 - pinch['thumb_index']) / 0.16)
    zero_tail_closed = avg(closed(s[name]) for name in ('middle', 'ring', 'pinky'))
    zero_loop = features.thumb_index_loop_score
    zero_thumb_open = clamp01((s['thumb'] - 0.60) / 0.20)
    zero_index_closed = clamp01((0.48 - s['index']) / 0.24)
    zero_any_close = clamp01((0.60 - features.thumb_index_any_ratio) / 0.26)
    collapsed_hand = clamp01((0.64 - non_thumb_open) / 0.24)
    tight_tips = clamp01((0.90 - avg(tips[name] for name in ('index', 'middle', 'ring', 'pinky'))) / 0.36)
    fist_thumb_closed = max(clamp01((0.50 - s['thumb']) / 0.24), clamp01((curl['thumb'] - 0.42) / 0.26))
    fist_fold_profile = avg(
        clamp01((features.bend_angles[name][0] - 148.0) / 24.0)
        * clamp01((116.0 - features.bend_angles[name][1]) / 36.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    fist_retraction = avg(
        clamp01((0.05 - (features.wrist_tip_ratios[name] - features.wrist_pip_ratios[name])) / 0.14)
        for name in ('index', 'middle', 'ring', 'pinky')
    )
    compact_curl_gate = clamp01((compact_cluster + compact_tips + fist_retraction - 1.05) / 0.55)
    false_claw_penalty = (
        clamp01((hook_profile - 0.34) / 0.30)
        * clamp01((non_thumb_ext - 0.36) / 0.26)
        * clamp01((0.34 - compact_cluster) / 0.22)
    )
    curled_shell_penalty = avg(
        clamp01((100.0 - features.bend_angles[name][0]) / 40.0)
        * clamp01((features.bend_angles[name][1] - 104.0) / 24.0)
        for name in ('index', 'middle', 'ring', 'pinky')
    )

    return {
        'open_hand': clamp01(
            0.46 * all_open
            + 0.18 * non_thumb_ext
            + 0.14 * non_thumb_open
            + 0.10 * relaxed_spread
            + 0.12 * thumb_open_bonus
            - 0.12 * zero_pinch
            - 0.10 * non_thumb_curl
        ),
        'fist': clamp01(
            (
                0.12 * all_closed
                + 0.12 * compact_tips
                + 0.10 * collapsed_hand
                + 0.08 * tight_tips
                + 0.12 * fist_fold_profile
                + 0.12 * fist_retraction
                + 0.20 * curled_profile
                + 0.08 * compact_cluster
                + 0.04 * clamp01((0.70 - pinch['thumb_index']) / 0.28)
                + 0.02 * fist_thumb_closed
            )
            * (0.82 + 0.18 * compact_curl_gate)
            * (1.00 - 0.32 * false_claw_penalty)
            * (1.00 - 0.82 * curled_shell_penalty)
        ),
        'zero': clamp01(
            0.26 * zero_loop
            + 0.16 * zero_pinch
            + 0.16 * zero_tail_closed
            + 0.18 * zero_thumb_open
            + 0.16 * zero_index_closed
            + 0.08 * zero_any_close
        ),
    }
