from __future__ import annotations

from typing import Dict

from ..analysis.geometry import clamp01, distance
from ..models import GestureCandidate, HandReading


_OPEN_WEIGHTS = {
    "fully_open": 1.0,
    "partially_curled": 0.42,
    "mostly_curled": 0.10,
    "closed": 0.0,
}

_CLOSED_WEIGHTS = {
    "fully_open": 0.0,
    "partially_curled": 0.34,
    "mostly_curled": 0.84,
    "closed": 1.0,
}


class StaticGestureRecognizer:
    actual_labels: tuple[str, ...] = (
        "open_hand",
        "fist",
        "zero",
        "ok",
        "one",
        "two",
        "three",
        "four",
        "mute",
        "volume_pose",
        "wheel_pose",
        "chrome_wheel_pose",
    )
    debug_labels: tuple[str, ...] = (
        "finger_together",
        "finger_apart",
    )

    def _openish(self, hand: HandReading, name: str) -> float:
        finger = hand.fingers[name]
        return clamp01(0.58 * finger.openness + 0.42 * _OPEN_WEIGHTS[finger.state])

    def _closedish(self, hand: HandReading, name: str) -> float:
        finger = hand.fingers[name]
        return clamp01(0.52 * finger.curl + 0.48 * _CLOSED_WEIGHTS[finger.state])

    def _volume_primary_gate(self, hand: HandReading, name: str) -> float:
        finger = hand.fingers[name]
        if finger.state == "fully_open":
            return 1.0
        if (
            finger.state == "partially_curled"
            and finger.openness >= 0.56
            and finger.curl <= 0.52
            and finger.bend_proximal >= 122.0
            and finger.bend_distal >= 138.0
        ):
            return clamp01(0.62 + 0.38 * (finger.openness - 0.56) / 0.20)
        return 0.0

    def _mute_pinky_gate(self, hand: HandReading) -> float:
        finger = hand.fingers["pinky"]
        return max(
            clamp01((self._openish(hand, "pinky") - 0.58) / 0.18),
            clamp01((finger.openness - 0.46) / 0.16)
            * max(
                clamp01((finger.reach - 0.18) / 0.24),
                clamp01((finger.palm_distance - 0.86) / 0.26),
                clamp01((finger.bend_distal - 138.0) / 22.0),
            ),
        )

    def score(self, hand: HandReading) -> Dict[str, float]:
        openish = {name: self._openish(hand, name) for name in hand.fingers}
        closedish = {name: self._closedish(hand, name) for name in hand.fingers}
        spread = hand.spreads
        palm_scale = max(hand.palm.scale, 1e-6)
        thumb_index_ratio = distance(hand.landmarks[4], hand.landmarks[8]) / palm_scale
        thumb_index_side_ratio = min(
            distance(hand.landmarks[4], hand.landmarks[5]),
            distance(hand.landmarks[4], hand.landmarks[6]),
            distance(hand.landmarks[3], hand.landmarks[5]),
        ) / palm_scale

        hook_profile = sum(
            clamp01((hand.fingers[name].bend_proximal - 150.0) / 20.0)
            * clamp01((124.0 - hand.fingers[name].bend_distal) / 48.0)
            * clamp01((hand.fingers[name].reach - 0.02) / 0.16)
            for name in ("index", "middle", "ring", "pinky")
        ) / 4.0

        non_thumb_extended = sum(openish[name] for name in ("index", "middle", "ring", "pinky")) / 4.0
        non_thumb_closed = sum(closedish[name] for name in ("index", "middle", "ring", "pinky")) / 4.0
        tail_closed = sum(closedish[name] for name in ("middle", "ring", "pinky")) / 3.0
        all_open = (non_thumb_extended * 4.0 + openish["thumb"]) / 5.0
        non_thumb_closed_count = sum(1 for name in ("index", "middle", "ring", "pinky") if hand.fingers[name].state == "closed")
        non_thumb_folded_count = sum(1 for name in ("index", "middle", "ring", "pinky") if hand.fingers[name].state in {"mostly_curled", "closed"})
        thumb_open_core = clamp01((openish["thumb"] - 0.70) / 0.20)
        pinky_open_core = self._mute_pinky_gate(hand)
        thumb_fold_gate = (
            1.0 if hand.fingers["thumb"].state in {"mostly_curled", "closed"}
            else 0.25 if hand.fingers["thumb"].state == "partially_curled" and hand.fingers["thumb"].openness <= 0.34
            else 0.0
        )
        volume_primary = min(openish["index"], openish["middle"])
        volume_rest_fold = sum(closedish[name] for name in ("thumb", "ring", "pinky")) / 3.0
        volume_index_gate = max(
            clamp01((hand.fingers["index"].openness - 0.64) / 0.18),
            self._volume_primary_gate(hand, "index"),
        )
        volume_middle_gate = max(
            clamp01((hand.fingers["middle"].openness - 0.64) / 0.18),
            self._volume_primary_gate(hand, "middle"),
        )
        volume_open_gate = min(volume_index_gate, volume_middle_gate)
        volume_state_gate = self._volume_primary_gate(hand, "index") * self._volume_primary_gate(hand, "middle")
        volume_spacing_gate = max(
            spread["index_middle"].together_strength,
            clamp01((0.50 - spread["index_middle"].distance) / 0.20),
        )
        volume_rest_state_gate = sum(
            (
                1.0 if name == "thumb" and hand.fingers[name].state in {"mostly_curled", "closed"}
                else 0.35 if name == "thumb" and hand.fingers[name].state == "partially_curled" and hand.fingers[name].openness <= 0.36
                else 1.0 if name != "thumb" and hand.fingers[name].state in {"mostly_curled", "closed"}
                else 0.0
            )
            for name in ("thumb", "ring", "pinky")
        ) / 3.0
        fist_state_gate = max(
            clamp01((non_thumb_closed_count - 1.8) / 1.6),
            0.48 * clamp01((non_thumb_folded_count - 3.0) / 1.0),
        )
        fist_thumb_gate = 1.0 if hand.fingers["thumb"].state in {"mostly_curled", "closed", "partially_curled"} else 0.0
        ok_loop_gate = clamp01((0.27 - thumb_index_ratio) / 0.16)
        ok_loop_side_gate = clamp01((thumb_index_side_ratio - 0.58) / 0.20)
        ok_tail_open = sum(openish[name] for name in ("middle", "ring", "pinky")) / 3.0
        ok_tail_open_gate = clamp01((min(openish[name] for name in ("middle", "ring", "pinky")) - 0.58) / 0.22)
        ok_index_fold = clamp01((closedish["index"] - 0.62) / 0.22)
        ok_index_gate = max(
            1.0 if hand.fingers["index"].state in {"mostly_curled", "closed", "partially_curled"} else 0.0,
            clamp01((0.76 - hand.fingers["index"].openness) / 0.22),
        )
        ok_core = min(ok_loop_gate, ok_loop_side_gate, ok_tail_open_gate, ok_index_gate)
        wheel_triplet_open = (openish["thumb"] + openish["index"] + openish["pinky"]) / 3.0
        wheel_fold_pair = (closedish["middle"] + closedish["ring"]) / 2.0
        wheel_open_gate = min(
            clamp01((openish["thumb"] - 0.42) / 0.26),
            clamp01((openish["index"] - 0.56) / 0.20),
            clamp01((openish["pinky"] - 0.44) / 0.24),
        )
        wheel_fold_gate = min(
            clamp01((closedish["middle"] - 0.58) / 0.20),
            clamp01((closedish["ring"] - 0.58) / 0.20),
        )
        wheel_shape_gate = min(
            max(
                spread["thumb_index"].apart_strength,
                clamp01((spread["thumb_index"].distance - 0.44) / 0.24),
            ),
            clamp01((spread["ring_pinky"].distance - 0.18) / 0.20),
        )
        zero_thumb_out_fold_gate = min(
            clamp01((min(closedish[name] for name in ("index", "middle", "ring", "pinky")) - 0.72) / 0.18),
            clamp01((non_thumb_closed - 0.78) / 0.14),
        )
        zero_thumb_out_thumb_gate = min(
            clamp01((openish["thumb"] - 0.70) / 0.18),
            clamp01((hand.fingers["thumb"].palm_distance - 0.72) / 0.24),
        )
        zero_thumb_out_span_gate = min(
            clamp01((thumb_index_ratio - 0.90) / 0.55),
            clamp01((thumb_index_side_ratio - 0.82) / 0.36),
        )
        zero_loop_score = clamp01(
            0.28 * clamp01((0.34 - thumb_index_ratio) / 0.18)
            + 0.24 * clamp01((thumb_index_side_ratio - 0.66) / 0.20)
            + 0.14 * clamp01((hand.fingers["thumb"].palm_distance - 0.18) / 0.18)
            + 0.14 * clamp01((0.88 - openish["thumb"]) / 0.36)
            + 0.16 * closedish["index"]
            + 0.20 * tail_closed
        )
        zero_thumb_out_score = clamp01(
            (
                0.34 * non_thumb_closed
                + 0.20 * openish["thumb"]
                + 0.18 * zero_thumb_out_fold_gate
                + 0.16 * zero_thumb_out_thumb_gate
                + 0.12 * zero_thumb_out_span_gate
            )
            * (0.18 + 0.82 * zero_thumb_out_fold_gate)
            * (0.20 + 0.80 * zero_thumb_out_thumb_gate)
            * (0.24 + 0.76 * zero_thumb_out_span_gate)
        )
        chrome_wheel_open_pair = (openish["index"] + openish["pinky"]) / 2.0
        chrome_wheel_fold_core = (closedish["thumb"] + closedish["middle"] + closedish["ring"]) / 3.0
        chrome_wheel_open_gate = min(
            clamp01((openish["index"] - 0.56) / 0.20),
            clamp01((openish["pinky"] - 0.42) / 0.24),
        )
        chrome_wheel_fold_gate = min(
            clamp01((closedish["thumb"] - 0.34) / 0.24),
            clamp01((closedish["middle"] - 0.58) / 0.20),
            clamp01((closedish["ring"] - 0.58) / 0.20),
        )
        chrome_wheel_shape_gate = min(
            max(
                spread["index_middle"].apart_strength,
                clamp01((spread["index_middle"].distance - 0.18) / 0.18),
            ),
            max(
                spread["ring_pinky"].apart_strength,
                clamp01((spread["ring_pinky"].distance - 0.16) / 0.16),
            ),
        )

        scores = {
            "open_hand": clamp01(
                0.72 * all_open
                + 0.20 * spread["index_middle"].apart_strength
                + 0.12 * spread["middle_ring"].apart_strength
                + 0.08 * spread["ring_pinky"].apart_strength
            ),
            "fist": clamp01(
                (
                    0.54 * non_thumb_closed
                    + 0.18 * closedish["thumb"]
                    + 0.16 * clamp01((0.92 - sum(hand.fingers[name].palm_distance for name in ("index", "middle", "ring", "pinky")) / 4.0) / 0.34)
                    + 0.12 * clamp01((0.28 - thumb_index_ratio) / 0.16)
                )
                * (1.0 - 0.78 * hook_profile)
                * (0.18 + 0.82 * fist_state_gate)
                * (0.40 + 0.60 * fist_thumb_gate)
            ),
            "zero": max(zero_loop_score, zero_thumb_out_score),
            "ok": clamp01(
                0.28 * ok_loop_gate
                + 0.18 * ok_loop_side_gate
                + 0.24 * ok_tail_open
                + 0.16 * ok_index_fold
                + 0.14 * ok_tail_open_gate
            ),
            "one": clamp01(
                0.44 * openish["index"]
                + 0.26 * sum(closedish[name] for name in ("middle", "ring", "pinky")) / 3.0
                + 0.18 * closedish["thumb"]
                + 0.12 * spread["index_middle"].apart_strength
            ),
            "two": clamp01(
                0.34 * min(openish["index"], openish["middle"])
                + 0.22 * closedish["ring"]
                + 0.16 * closedish["pinky"]
                + 0.16 * spread["index_middle"].apart_strength
                + 0.12 * closedish["thumb"]
            ),
            "three": clamp01(
                0.28 * min(openish["index"], openish["middle"], openish["ring"])
                + 0.22 * closedish["pinky"]
                + 0.18 * closedish["thumb"]
                + 0.16 * spread["index_middle"].apart_strength
                + 0.16 * spread["middle_ring"].apart_strength
            ),
            "four": clamp01(
                0.54 * non_thumb_extended
                + 0.18 * closedish["thumb"]
                + 0.10 * thumb_fold_gate
                + 0.12 * spread["index_middle"].apart_strength
                + 0.12 * spread["middle_ring"].apart_strength
            ),
            "mute": clamp01(
                0.26 * thumb_open_core
                + 0.26 * pinky_open_core
                + 0.24 * min(thumb_open_core, pinky_open_core)
                + 0.24 * sum(closedish[name] for name in ("index", "middle", "ring")) / 3.0
            ),
            "volume_pose": clamp01(
                (
                    0.26 * volume_primary
                    + 0.24 * volume_rest_fold
                    + 0.18 * volume_open_gate
                    + 0.20 * volume_rest_state_gate
                    + 0.12 * clamp01((volume_primary - max(openish["ring"], openish["pinky"]) - 0.12) / 0.22)
                )
                * (0.18 + 0.82 * volume_spacing_gate)
                * (0.20 + 0.80 * volume_state_gate)
            ),
            "wheel_pose": clamp01(
                (
                    0.28 * wheel_triplet_open
                    + 0.26 * wheel_fold_pair
                    + 0.20 * wheel_open_gate
                    + 0.16 * wheel_fold_gate
                    + 0.10 * wheel_shape_gate
                )
                * (0.18 + 0.82 * wheel_open_gate)
                * (0.18 + 0.82 * wheel_fold_gate)
            ),
            "chrome_wheel_pose": clamp01(
                (
                    0.30 * chrome_wheel_open_pair
                    + 0.28 * chrome_wheel_fold_core
                    + 0.22 * chrome_wheel_open_gate
                    + 0.12 * chrome_wheel_fold_gate
                    + 0.08 * chrome_wheel_shape_gate
                )
                * (0.18 + 0.82 * chrome_wheel_open_gate)
                * (0.18 + 0.82 * chrome_wheel_fold_gate)
            ),
            "finger_together": clamp01(
                0.56 * non_thumb_extended
                + 0.14 * spread["index_middle"].together_strength
                + 0.15 * spread["middle_ring"].together_strength
                + 0.15 * spread["ring_pinky"].together_strength
            ),
            "finger_apart": clamp01(
                0.56 * non_thumb_extended
                + 0.14 * spread["index_middle"].apart_strength
                + 0.15 * spread["middle_ring"].apart_strength
                + 0.15 * spread["ring_pinky"].apart_strength
            ),
        }

        if hand.fingers["thumb"].state == "fully_open":
            scores["four"] *= 0.38
            scores["fist"] *= 0.60
        if thumb_fold_gate <= 0.0:
            scores["four"] *= 0.08
        elif thumb_fold_gate < 0.40:
            scores["four"] *= 0.30
        if hook_profile > 0.22:
            scores["fist"] *= 0.22
        if hook_profile > 0.18 and sum(1 for name in ("index", "middle", "ring", "pinky") if hand.fingers[name].state == "partially_curled") >= 2:
            scores["three"] *= 0.18
            scores["open_hand"] *= 0.24
            scores["four"] *= 0.18
        if non_thumb_extended < 0.52:
            scores["open_hand"] *= 0.40
            scores["four"] *= 0.54
        if thumb_open_core < 0.24 or pinky_open_core < 0.18:
            scores["mute"] *= 0.34
        if spread["index_middle"].distance > 0.62:
            scores["volume_pose"] *= 0.18
        if spread["index_middle"].apart_strength > 0.72:
            scores["volume_pose"] *= 0.16
        if volume_rest_fold < 0.42 or volume_open_gate < 0.12:
            scores["volume_pose"] *= 0.42
        if volume_state_gate <= 0.0:
            scores["two"] *= 0.28
            scores["volume_pose"] *= 0.08
        if thumb_index_side_ratio < 0.60:
            zero_loop_score *= 0.12
        if thumb_index_ratio > 0.40:
            zero_loop_score *= 0.10
        if zero_thumb_out_fold_gate <= 0.0:
            zero_thumb_out_score *= 0.08
        if zero_thumb_out_thumb_gate <= 0.0:
            zero_thumb_out_score *= 0.10
        if zero_thumb_out_span_gate <= 0.0:
            zero_thumb_out_score *= 0.12
        elif zero_thumb_out_span_gate < 0.24:
            zero_thumb_out_score *= 0.40
        scores["zero"] = max(zero_loop_score, zero_thumb_out_score)
        if thumb_index_ratio > 0.32 or thumb_index_side_ratio < 0.54:
            scores["ok"] *= 0.08
        if ok_tail_open_gate < 0.16 or ok_index_gate <= 0.0:
            scores["ok"] *= 0.12
        if tail_closed > 0.42:
            scores["ok"] *= 0.10
        if ok_core > 0.14:
            scores["open_hand"] *= 0.12
            scores["four"] *= 0.26
        if wheel_open_gate <= 0.0 or wheel_fold_gate <= 0.0:
            scores["wheel_pose"] *= 0.10
        if openish["middle"] > 0.42 or openish["ring"] > 0.42:
            scores["wheel_pose"] *= 0.16
        if openish["index"] < 0.52 or openish["pinky"] < 0.40:
            scores["wheel_pose"] *= 0.20
        if thumb_open_core < 0.24:
            scores["wheel_pose"] *= 0.24
        if scores["wheel_pose"] > 0.36:
            scores["mute"] *= 0.52
            scores["three"] *= 0.46
        if chrome_wheel_open_gate <= 0.0 or chrome_wheel_fold_gate <= 0.0:
            scores["chrome_wheel_pose"] *= 0.10
        if openish["middle"] > 0.40 or openish["ring"] > 0.40:
            scores["chrome_wheel_pose"] *= 0.14
        if openish["thumb"] > 0.54:
            scores["chrome_wheel_pose"] *= 0.24
        if scores["chrome_wheel_pose"] > 0.34:
            scores["one"] *= 0.34
            scores["mute"] *= 0.34
            scores["wheel_pose"] *= 0.28
        return scores

    def predict(self, hand: HandReading) -> tuple[str, float, tuple[GestureCandidate, ...], Dict[str, float]]:
        scores = self.score(hand)
        actual_ranked = sorted(
            (GestureCandidate(label, scores[label], "static") for label in self.actual_labels),
            key=lambda item: item.score,
            reverse=True,
        )
        ranked = sorted((GestureCandidate(label, score, "static") for label, score in scores.items()), key=lambda item: item.score, reverse=True)
        best = actual_ranked[0] if actual_ranked else GestureCandidate("neutral", 0.0, "static")
        second_score = actual_ranked[1].score if len(actual_ranked) > 1 else 0.0
        margin = best.score - second_score
        confidence = clamp01(best.score * 0.72 + max(margin, 0.0) * 0.28 + hand.shape_confidence * 0.12)
        if best.score < 0.56 or margin < 0.025:
            return "neutral", confidence * 0.60, tuple(ranked[:5]), scores
        return best.label, confidence, tuple(ranked[:5]), scores
