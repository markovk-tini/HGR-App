from __future__ import annotations

import math
from typing import Dict

import numpy as np


FINGER_X = {
    'index': 0.22,
    'middle': 0.08,
    'ring': -0.06,
    'pinky': -0.20,
}


def _as_array(x: float, y: float, z: float = 0.0) -> np.ndarray:
    return np.array([x, y, z], dtype=np.float32)


def _rotate(points: np.ndarray, degrees: float) -> np.ndarray:
    if abs(degrees) < 1e-6:
        return points.copy()
    center = points[[0, 5, 9, 13, 17]].mean(axis=0)
    radians = math.radians(degrees)
    rotation = np.array(
        [
            [math.cos(radians), -math.sin(radians)],
            [math.sin(radians), math.cos(radians)],
        ],
        dtype=np.float32,
    )
    rotated = points.copy()
    rotated_xy = (points[:, :2] - center[:2]) @ rotation.T + center[:2]
    rotated[:, :2] = rotated_xy
    return rotated


def translate_landmarks(points: np.ndarray, *, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> np.ndarray:
    shifted = points.copy()
    shifted[:, 0] += dx
    shifted[:, 1] += dy
    shifted[:, 2] += dz
    return shifted


def make_landmarks(
    finger_states: Dict[str, str],
    *,
    thumb_state: str = 'open',
    pinch_thumb_index: bool = False,
    spread: str = 'normal',
    rotation_degrees: float = 0.0,
) -> np.ndarray:
    points = np.zeros((21, 3), dtype=np.float32)
    points[0] = _as_array(0.0, 0.0)

    thumb_open = {
        1: _as_array(-0.12, -0.06),
        2: _as_array(-0.24, -0.16),
        3: _as_array(-0.34, -0.26),
        4: _as_array(-0.42, -0.34),
    }
    thumb_folded = {
        1: _as_array(-0.08, -0.03),
        2: _as_array(0.02, -0.02),
        3: _as_array(0.12, -0.03),
        4: _as_array(0.20, -0.10),
    }
    thumb_mute = {
        1: _as_array(-0.10, -0.03),
        2: _as_array(-0.05, 0.03),
        3: _as_array(0.02, 0.07),
        4: _as_array(0.16, 0.10),
    }
    thumb_template = thumb_open
    if thumb_state == 'closed':
        thumb_template = thumb_folded
    elif thumb_state == 'mute':
        thumb_template = thumb_mute
    for idx, value in thumb_template.items():
        points[idx] = value

    spread_offsets = {
        'normal': {'index': 0.06, 'middle': 0.02, 'ring': -0.02, 'pinky': -0.06},
        'apart': {'index': 0.22, 'middle': 0.08, 'ring': -0.10, 'pinky': -0.24},
        'together': {'index': 0.02, 'middle': 0.01, 'ring': 0.00, 'pinky': -0.01},
        'volume_together': {'index': -0.02, 'middle': 0.04, 'ring': 0.00, 'pinky': -0.01},
    }
    offsets = spread_offsets[spread]

    finger_indices = {
        'index': (5, 6, 7, 8),
        'middle': (9, 10, 11, 12),
        'ring': (13, 14, 15, 16),
        'pinky': (17, 18, 19, 20),
    }
    for name, indices in finger_indices.items():
        x = FINGER_X[name] + offsets[name]
        state = finger_states[name]
        mcp, pip, dip, tip = indices
        points[mcp] = _as_array(x, -0.22)
        if state == 'open':
            points[pip] = _as_array(x + 0.01, -0.55)
            points[dip] = _as_array(x + 0.02, -0.84)
            points[tip] = _as_array(x + 0.02, -1.10)
        elif state == 'hooked':
            points[pip] = _as_array(x + 0.01, -0.55)
            points[dip] = _as_array(x + 0.04, -0.78)
            points[tip] = _as_array(x + 0.10, -0.62)
        elif state == 'curled':
            points[pip] = _as_array(x + 0.04, -0.28)
            points[dip] = _as_array(x + 0.12, -0.08)
            points[tip] = _as_array(x + 0.04, 0.02)
        else:
            points[pip] = _as_array(x + 0.05, -0.12)
            points[dip] = _as_array(x + 0.15, 0.04)
            points[tip] = _as_array(x + 0.04, 0.10)

    if thumb_state == 'mute':
        points[20] = _as_array(-0.34, -0.52)
        points[19] = _as_array(-0.28, -0.34)
        points[18] = _as_array(-0.24, -0.18)

    if pinch_thumb_index:
        index_tip = points[8]
        thumb_anchor = points[4]
        midpoint = (index_tip + thumb_anchor) * 0.5
        points[4] = midpoint + _as_array(-0.03, 0.00)
        points[8] = midpoint + _as_array(0.03, 0.00)
        points[7] = midpoint + _as_array(0.01, -0.10)

    return _rotate(points, rotation_degrees)


def make_pose(name: str, *, rotation_degrees: float = 0.0, spread: str = 'normal') -> np.ndarray:
    mapping = {
        'open_hand': dict(finger_states={name: 'open' for name in ('index', 'middle', 'ring', 'pinky')}, thumb_state='open', spread=spread),
        'fist': dict(finger_states={name: 'closed' for name in ('index', 'middle', 'ring', 'pinky')}, thumb_state='closed', spread='together'),
        'zero': dict(finger_states={'index': 'curled', 'middle': 'closed', 'ring': 'closed', 'pinky': 'closed'}, thumb_state='open', pinch_thumb_index=True, spread='normal'),
        'ok': dict(finger_states={'index': 'curled', 'middle': 'open', 'ring': 'open', 'pinky': 'open'}, thumb_state='open', pinch_thumb_index=True, spread='apart'),
        'one': dict(finger_states={'index': 'open', 'middle': 'closed', 'ring': 'closed', 'pinky': 'closed'}, thumb_state='closed', spread='together'),
        'two': dict(finger_states={'index': 'open', 'middle': 'open', 'ring': 'closed', 'pinky': 'closed'}, thumb_state='closed', spread='apart'),
        'three': dict(finger_states={'index': 'open', 'middle': 'open', 'ring': 'open', 'pinky': 'closed'}, thumb_state='closed', spread='normal'),
        'four': dict(finger_states={'index': 'open', 'middle': 'open', 'ring': 'open', 'pinky': 'open'}, thumb_state='closed', spread='together'),
        'mute': dict(finger_states={'index': 'closed', 'middle': 'closed', 'ring': 'closed', 'pinky': 'open'}, thumb_state='mute', spread='normal'),
        'volume_pose': dict(finger_states={'index': 'open', 'middle': 'open', 'ring': 'closed', 'pinky': 'closed'}, thumb_state='closed', spread='volume_together'),
        'wheel_pose': dict(finger_states={'index': 'open', 'middle': 'closed', 'ring': 'closed', 'pinky': 'open'}, thumb_state='open', spread='apart'),
        'chrome_wheel_pose': dict(finger_states={'index': 'open', 'middle': 'closed', 'ring': 'closed', 'pinky': 'open'}, thumb_state='closed', spread='apart'),
        'claw': dict(finger_states={name: 'hooked' for name in ('index', 'middle', 'ring', 'pinky')}, thumb_state='open', spread='normal'),
        'finger_together': dict(finger_states={name: 'open' for name in ('index', 'middle', 'ring', 'pinky')}, thumb_state='closed', spread='together'),
        'finger_apart': dict(finger_states={name: 'open' for name in ('index', 'middle', 'ring', 'pinky')}, thumb_state='closed', spread='apart'),
    }
    params = mapping[name].copy()
    params['rotation_degrees'] = rotation_degrees
    if spread != 'normal' and name not in {'fist', 'two', 'finger_together', 'finger_apart'}:
        params['spread'] = spread
    return make_landmarks(**params)
