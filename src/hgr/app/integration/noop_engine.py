from __future__ import annotations

import math
import queue
import re
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ...debug.chrome_controller import ChromeController
from ...debug.chrome_gesture_router import ChromeGestureRouter
from ...debug.foreground_window import get_foreground_window_info, is_foreground_fullscreen
from ...debug.mouse_controller import MouseController
from ...debug.mouse_gesture import MouseGestureTracker
from ...debug.mouse_overlay import draw_mouse_control_box_overlay, draw_mouse_monitor_overlay
from ...voice.live_dictation import LiveDictationEvent, LiveDictationStreamer
from ...config.app_config import save_config
from ...debug.low_fps_suggestion_overlay import LowFpsSuggestionOverlay
from ...debug.screen_volume_overlay import ScreenVolumeOverlay
from ...debug.spotify_controller import SpotifyController
from ...debug.spotify_gesture_router import SpotifyGestureRouter
from ...debug.text_input_controller import TextInputController
from ...debug.voice_command_listener import VoiceCommandListener
from ...debug.volume_controller import VolumeController
from ...debug.volume_gesture import VolumeGestureTracker
from ...debug.youtube_controller import YouTubeController
from ...debug.youtube_gesture_router import YouTubeGestureRouter
from ...gesture.recognition.engine import GestureRecognitionEngine
from ...gesture.tracking.detector import HandDetector
from ...gesture.tracking.smoothing import AdaptiveLandmarkSmoother
from ...gesture.rendering.overlay import draw_hand_overlay
from ...gesture.ui.test_window import SpotifyWheelOverlay
from ...gesture.ui.voice_status_overlay import VoiceStatusOverlay
from ...voice.command_processor import VoiceCommandContext, VoiceCommandProcessor
from ...voice.dictation import DictationProcessor
from ...voice.grammar_corrector import CorrectionResult, GrammarCorrector
from ...voice.llama_server import LlamaServer
from ...voice.whisper_refiner import RefinementResult, WhisperRefiner
from ..camera.camera_utils import open_camera_by_index, open_phone_camera_url, open_preferred_or_first_available


_DICTATION_HALLUCINATION_STOPWORDS = {
    "the", "you", "and", "a", "to", "of", "is", "it", "so", "i",
    "uh", "um", "ah", "oh", "mm", "mhm", "hmm", "hm", "eh",
    "thanks", "thank", "bye", "okay", "ok",
}


# Voice commands recognized only when the committed utterance is EXACTLY one
# of these phrases (after punctuation strip + whitespace collapse + lowercase).
# The whole-utterance match is the safety — saying "I need a new line of code"
# in a sentence won't trigger because the commit contains more than just the
# command phrase. The user has to pause, say just the command, then pause.
_DICTATION_NEWLINE_COMMANDS = frozenset({
    "new line", "newline", "next line", "line break",
    "press enter", "press return", "hit enter", "enter key",
})
_DICTATION_PARAGRAPH_COMMANDS = frozenset({
    "new paragraph", "paragraph break",
})
_DICTATION_COMMAND_NORMALIZE_RE = re.compile(r"[^\w\s]+")


def _parse_dictation_command(text: str) -> Optional[str]:
    if not text:
        return None
    stripped = _DICTATION_COMMAND_NORMALIZE_RE.sub("", text)
    normalized = re.sub(r"\s+", " ", stripped).strip().lower()
    if not normalized:
        return None
    if normalized in _DICTATION_NEWLINE_COMMANDS:
        return "newline"
    if normalized in _DICTATION_PARAGRAPH_COMMANDS:
        return "paragraph"
    return None

_DICTATION_TRAILING_HALLUCINATIONS = {"the", "you", "and", "a"}

_WHISPER_STOCK_HALLUCINATIONS = (
    "good afternoon, everyone",
    "good afternoon everyone",
    "good morning, everyone",
    "good morning everyone",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "don't forget to subscribe",
    "bye-bye",
    "bye bye",
    "we'll be right back",
    "we will be right back",
    "see you in the next one",
    "see you next time",
)

_WHISPER_STOCK_PATTERNS = tuple(
    re.compile(r"\b" + re.escape(phrase) + r"\.?", re.IGNORECASE)
    for phrase in _WHISPER_STOCK_HALLUCINATIONS
) + (
    # Windows / MS Office ProgID hallucinations whisper pulls from training
    # data on low-signal trailing audio (e.g., mic bumps, keyboard noise).
    # The ProgID-like suffix ("MSWordDoc Word.Document.8" or similar) is the
    # distinguishing signal — legitimate phrases like "I opened a word
    # document" never include it, so the suffix is required.
    re.compile(
        r"\b(?:microsoft\s+)?(?:word|excel|powerpoint)\s+document"
        r"\s+ms(?:word|excel|powerpoint)doc[.\s]*"
        r"(?:word|excel|powerpoint)\.?document\.?\d*\.?",
        re.IGNORECASE,
    ),
)


_SENTENCE_SPLIT_RE = re.compile(r"[^.!?]+[.!?]+\s*|[^.!?]+$")


def _collapse_repeated_sentences(text: str) -> str:
    # Whisper-stream sometimes emits the same sentence 2-3x concatenated into
    # a single final (trailing-silence hallucination after a real utterance).
    parts = _SENTENCE_SPLIT_RE.findall(text)
    if len(parts) <= 1:
        return text
    out: list[str] = []
    last_norm = ""
    for part in parts:
        norm = re.sub(r"\s+", " ", part.strip(" .!?,;:\"'").lower())
        if norm and norm == last_norm:
            continue
        out.append(part)
        last_norm = norm
    return "".join(out).strip()


def _redecode_overlap(prev_text: str, new_text: str) -> bool:
    def _norm(tok: str) -> str:
        return tok.lower().strip(".,!?;:\"'")
    prev_toks = [t for t in (_norm(x) for x in prev_text.split()) if t]
    new_toks = [t for t in (_norm(x) for x in new_text.split()) if t]
    if len(prev_toks) < 2 or len(new_toks) < 2:
        return False

    # Pattern 1: tail-of-prev matches head-of-new (continuation re-decode).
    prev_tail = prev_toks[-6:]
    for k in (4, 3, 2):
        if len(new_toks) < k:
            continue
        new_head = new_toks[:k]
        for start in range(0, len(prev_tail) - k + 1):
            if prev_tail[start:start + k] == new_head:
                return True

    # Pattern 2: prefix re-decode — new and prev share a long common prefix
    # (whisper-stream re-emitting the same utterance with refined decoding,
    # e.g. "fox jumps over the laser" → "fox jumps over the lazy dog").
    prefix = 0
    for a, b in zip(prev_toks, new_toks):
        if a == b:
            prefix += 1
        else:
            break
    if prefix >= 3 and prefix >= int(min(len(prev_toks), len(new_toks)) * 0.5):
        return True

    # Pattern 3: suffix re-decode — new is mostly contained at the end of prev
    # (e.g. prev "The quick brown fox jumps over the lazy dog",
    #       new  "The fox jumps over the lazy dog").
    # Require first-token match so we don't wipe out glued prefixes like
    # "Hello there How are you" when only "How are you" is re-emitted.
    if (
        len(new_toks) >= 3
        and len(new_toks) <= len(prev_toks)
        and prev_toks[0] == new_toks[0]
    ):
        tail_window = prev_toks[-(len(new_toks) + 2):]
        # count tokens of new_toks that appear in tail_window in order
        i = 0
        for tok in tail_window:
            if i < len(new_toks) and tok == new_toks[i]:
                i += 1
        if i >= max(3, int(len(new_toks) * 0.7)):
            return True

    return False


def _strip_whisper_hallucinations(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    # second-pass cleanup in case stream filter missed it
    stripped = re.sub(r"[\[\(]\s*(?:no\s*audio|blank[_\s]*audio|silence|music|applause|laughter|inaudible|background\s+noise)\s*[\]\)]", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\.{2,}", ".", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if not stripped or re.fullmatch(r"[\s\.\,\-!?:;]+", stripped):
        return ""

    stripped = _collapse_repeated_sentences(stripped)

    stock_hit = False
    for pattern in _WHISPER_STOCK_PATTERNS:
        new_stripped, n = pattern.subn("", stripped)
        if n > 0:
            stock_hit = True
            stripped = new_stripped
    if stock_hit:
        stripped = re.sub(r"\s+", " ", stripped)
        stripped = re.sub(r"\s*[,.;:!?\-]+\s*$", "", stripped)
        stripped = re.sub(r"(?<=[.!?])\s*[,.;:!?\-]+", "", stripped)
        stripped = stripped.strip()

    if not stripped:
        return ""

    tokens = stripped.split()

    def _norm(tok: str) -> str:
        return tok.lower().strip(".,!?;:\"'")

    filtered = []
    for tok in tokens:
        rstripped = tok.rstrip(".,!?;:\"'")
        if len(rstripped) >= 2 and rstripped.endswith("-") and not rstripped.endswith("--"):
            continue
        filtered.append(tok)
    tokens = filtered

    cleaned = [t for t in (_norm(tok) for tok in tokens) if t]
    if cleaned and len(cleaned) <= 2 and all(tok in _DICTATION_HALLUCINATION_STOPWORDS for tok in cleaned):
        return ""

    deduped: list[str] = []
    for tok in tokens:
        key = _norm(tok)
        if deduped and key and key == _norm(deduped[-1]):
            continue
        deduped.append(tok)
    tokens = deduped

    while len(tokens) >= 3:
        tail = _norm(tokens[-1])
        if tail in _DICTATION_TRAILING_HALLUCINATIONS:
            tokens.pop()
        else:
            break
    return " ".join(tokens).strip()


@dataclass(frozen=True)
class ActionEvent:
    timestamp: float
    label: str
    display_text: str
    undoable: bool = False
    is_undo: bool = False


_UNDO_LABEL_PAIRS = {
    "spotify_next": "spotify_previous",
    "spotify_previous": "spotify_next",
    "spotify_toggle": "spotify_toggle",
    "spotify_shuffle": "spotify_shuffle",
    "spotify_repeat": "spotify_repeat",
    "youtube_next": "youtube_previous",
    "youtube_previous": "youtube_next",
    "youtube_toggle": "youtube_toggle",
    "chrome_back": "chrome_forward",
    "chrome_forward": "chrome_back",
}


class GestureWorker(QObject):
    status_changed = Signal(str)
    command_detected = Signal(str)
    camera_selected = Signal(str)
    error_occurred = Signal(str)
    running_state_changed = Signal(bool)
    debug_frame_ready = Signal(object, object)
    save_prompt_completed = Signal(object)
    action_history_changed = Signal(object)
    dictation_stream_ready = Signal(str)

    _LOW_FPS_AUTO_THRESHOLD = 18.0
    _LOW_FPS_AUTO_ENTER_SECONDS = 4.0
    _LOW_FPS_AUTO_EXIT_SECONDS = 6.0
    _FORCED_TEST_FPS_TARGET = 10.0
    _NORMAL_PROCESS_WIDTH = 960
    _LOW_FPS_PROCESS_WIDTH = 384
    _FULLSCREEN_POLL_INTERVAL = 1.0
    # Suggestion overlay: triggered when measured FPS stays below 15 for
    # longer than 10 seconds. After the user dismisses (X, left-fist, or
    # auto-dismiss), we wait this many seconds before re-offering.
    _LOW_FPS_SUGGEST_THRESHOLD = 15.0
    _LOW_FPS_SUGGEST_ENTER_SECONDS = 10.0
    _LOW_FPS_SUGGEST_COOLDOWN_SECONDS = 300.0

    def __init__(self, config, camera_index_override: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.camera_index_override = camera_index_override
        self._running = False
        self._cap = None
        self._camera_info = None
        self.engine: GestureRecognitionEngine | None = None
        self._last_status_text = ""
        self._last_spotify_action = "-"
        self._last_chrome_action = "-"
        self._last_time = time.time()
        self._fps = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0
        self._low_fps_active = bool(getattr(config, "low_fps_mode", False))
        self._low_fps_below_since: float | None = None
        self._low_fps_above_since: float | None = None
        self._low_fps_auto_engaged = False
        self._low_fps_last_process = 0.0
        # Suggestion overlay bookkeeping (separate from auto-engage timing).
        self._low_fps_suggest_below_since: float | None = None
        self._low_fps_suggest_cooldown_until = 0.0
        self._low_fps_suggest_visible = False
        self._fullscreen_foreground_active = False
        self._fullscreen_foreground_process = ""
        self._fullscreen_check_last = 0.0
        # Phone-camera-via-QR capture (owned by MainWindow / PhoneCameraServer).
        # None when no phone is connected; set by set_phone_camera_capture().
        self._phone_camera_capture = None

        self.low_fps_suggestion_overlay = LowFpsSuggestionOverlay()
        self.low_fps_suggestion_overlay.activateRequested.connect(self._handle_low_fps_suggestion_activate)
        self.low_fps_suggestion_overlay.dismissed.connect(self._handle_low_fps_suggestion_dismissed)

        self.volume_controller = VolumeController()
        self.volume_overlay = ScreenVolumeOverlay(config)
        self.volume_overlay.attach_controller(self.volume_controller)
        self.voice_status_overlay = VoiceStatusOverlay(config)
        try:
            self.voice_status_overlay.selectionChosen.connect(self._handle_voice_overlay_selection)
        except Exception:
            pass
        self.dictation_stream_ready.connect(self._on_dictation_stream_ready)
        self.volume_tracker = VolumeGestureTracker()
        self._volume_message = self.volume_controller.message
        self._volume_mode_active = False
        self._volume_level: float | None = self.volume_controller.get_level()
        self._volume_status_text = "idle"
        self._volume_muted = self._read_system_mute()
        self._volume_overlay_visible = False
        self._mute_block_until = 0.0
        self._volume_dual_active = False
        self._volume_app_level: float | None = None
        self._volume_app_label = ""
        self._volume_app_process = ""
        self._volume_bar_selected = "sys"
        self._volume_init_palm_x: float | None = None
        self._volume_app_check_until = 0.0
        self._volume_nudge_next_at = 0.0
        self._volume_nudge_last_dir = 0
        self._youtube_volume_step_next_at = 0.0
        self._chrome_active_cache = False
        self._chrome_active_cache_until = 0.0
        self._spotify_active_cache = False
        self._spotify_active_cache_until = 0.0
        self._chrome_wheel_candidate = "neutral"
        self._chrome_wheel_candidate_since = 0.0
        self._chrome_wheel_visible = False
        self._chrome_wheel_anchor = None
        self._chrome_wheel_selected_key: str | None = None
        self._chrome_wheel_selected_since = 0.0
        self._chrome_wheel_pose_grace_until = 0.0
        self._chrome_wheel_cooldown_until = 0.0
        self._chrome_wheel_cursor_offset: tuple[float, float] | None = None
        self._spotify_wheel_candidate = "neutral"
        self._spotify_wheel_candidate_since = 0.0
        self._spotify_wheel_visible = False
        self._spotify_wheel_anchor = None
        self._spotify_wheel_selected_key: str | None = None
        self._spotify_wheel_selected_since = 0.0
        self._spotify_wheel_pose_grace_until = 0.0
        self._spotify_wheel_cooldown_until = 0.0
        self._spotify_wheel_cursor_offset: tuple[float, float] | None = None
        self._youtube_wheel_candidate = "neutral"
        self._youtube_wheel_candidate_since = 0.0
        self._youtube_wheel_visible = False
        self._youtube_wheel_anchor = None
        self._youtube_wheel_selected_key: str | None = None
        self._youtube_wheel_selected_since = 0.0
        self._youtube_wheel_pose_grace_until = 0.0
        self._youtube_wheel_cooldown_until = 0.0
        self._youtube_wheel_cursor_offset: tuple[float, float] | None = None
        self.spotify_wheel_overlay = SpotifyWheelOverlay(config)
        self.chrome_wheel_overlay = SpotifyWheelOverlay(config)
        self.youtube_wheel_overlay = SpotifyWheelOverlay(config)
        self.mouse_controller = MouseController()
        self.mouse_tracker = self._build_mouse_tracker()
        self._last_mouse_update = SimpleNamespace(
            mode_enabled=False,
            cursor_position=None,
            left_click=False,
            left_press=False,
            left_release=False,
            right_click=False,
            scroll_steps=0,
        )
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"

        self.chrome_controller = ChromeController()
        self.chrome_router = ChromeGestureRouter(static_hold_seconds=0.5, static_cooldown_seconds=1.5, dynamic_cooldown_seconds=1.5)
        self._chrome_mode_enabled = False
        self._chrome_control_text = self.chrome_controller.message

        self.spotify_controller = SpotifyController()
        self.spotify_router = SpotifyGestureRouter(static_hold_seconds=0.5, static_cooldown_seconds=1.5, dynamic_cooldown_seconds=1.5)
        self._spotify_control_text = self.spotify_controller.message

        self.youtube_controller = YouTubeController(volume_controller=self.volume_controller)
        self.youtube_router = YouTubeGestureRouter(static_hold_seconds=0.5, static_cooldown_seconds=1.5, dynamic_cooldown_seconds=1.0)
        self._youtube_control_text = "youtube idle"
        self._youtube_mode_info = "off"
        self._youtube_mode_prev_active = False
        self._chrome_mode_prev_active = False
        self._last_chrome_action_counter = 0
        self._last_spotify_action_counter = 0
        self._last_youtube_action_counter = 0

        self._action_history: deque[ActionEvent] = deque(maxlen=20)
        self._action_history_lock = threading.Lock()
        self._volume_session_prev_level: float | None = None
        self._volume_session_prev_app_level: float | None = None
        self._spotify_info_text = "-"
        self._spotify_vol_lock = threading.Lock()
        self._spotify_vol_target: int | None = None
        self._spotify_vol_last_sent: int | None = None
        self._spotify_vol_worker: threading.Thread | None = None

        self.voice_listener = VoiceCommandListener(
            preferred_input_device=getattr(config, "preferred_microphone_name", None),
            input_gain=getattr(config, "mic_input_gain", 1.0),
        )
        self.voice_processor = VoiceCommandProcessor(
            chrome_controller=self.chrome_controller,
            spotify_controller=self.spotify_controller,
        )
        self.live_dictation_streamer = LiveDictationStreamer(
            preferred_microphone_name=getattr(config, "preferred_microphone_name", None),
        )
        self.text_input_controller = TextInputController()
        self.dictation_processor = DictationProcessor()
        self.llama_server = LlamaServer()
        try:
            print(f"[hgr] llama_server: available={self.llama_server.available} backend={self.llama_server.backend} message={self.llama_server.message}")
        except Exception:
            pass
        self.grammar_corrector = GrammarCorrector(server=self.llama_server)
        import os as _os_refiner
        # Refiner is opt-IN: it duplicates text when streamer emits multiple
        # finals mid-utterance. Set HGR_ENABLE_WHISPER_REFINER=1 to test.
        self._refiner_enabled = _os_refiner.getenv("HGR_ENABLE_WHISPER_REFINER", "").strip() in {"1", "true", "yes"}
        if self._refiner_enabled:
            self.whisper_refiner = WhisperRefiner(on_refinement=self._apply_refinement)
            try:
                print(f"[hgr] whisper_refiner: available={self.whisper_refiner.available} backend={self.whisper_refiner.backend} message={self.whisper_refiner.message}")
            except Exception:
                pass
        else:
            self.whisper_refiner = None  # type: ignore[assignment]
            print("[hgr] whisper_refiner: disabled via HGR_DISABLE_WHISPER_REFINER")
        self._corrector_pending_text = ""
        self._corrector_lock = threading.Lock()
        self._corrector_applied_message = ""
        self._dictation_state: dict | None = None
        self._app_hint_thread: threading.Thread | None = None
        self._prime_voice_app_hints_async()
        self._prime_voice_runtime_async()
        self._voice_control_text = self.voice_listener.message
        self._voice_heard_text = "-"
        self._voice_candidate = "neutral"
        self._voice_candidate_since = 0.0
        self._voice_cooldown_until = 0.0
        self._voice_latched_label: str | None = None
        self._voice_one_two_triggered_at: float = 0.0
        self._left_hand_prediction = None
        self._voice_queue: queue.Queue[tuple[int, object]] = queue.Queue()
        self._voice_thread: threading.Thread | None = None
        self._voice_request_id = 0
        self._voice_stop_event: threading.Event | None = None
        self._voice_listening = False
        self._dictation_active = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_display_text = "-"
        self._dictation_toggle_release_required = False
        self._dictation_stop_rearm_at = 0.0
        self._dictation_release_candidate_since = 0.0
        self._tutorial_mode_enabled = False
        self._tutorial_step_key: str | None = None
        self._drawing_mode_enabled = False
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_toggle_cooldown_until = 0.0
        self._drawing_cursor_norm: tuple[float, float] | None = None
        self._drawing_tool = "hidden"
        self._drawing_control_text = "drawing mode off"
        self._drawing_render_target = "screen"
        self._drawing_brush_hex = str(getattr(config, "accent_color", "#1DE9B6") or "#1DE9B6")
        self._drawing_brush_thickness = 8
        self._drawing_eraser_thickness = 18
        self._drawing_eraser_mode = "normal"
        self._camera_draw_canvas: np.ndarray | None = None
        self._camera_draw_history: list[tuple[np.ndarray, list[dict], bool]] = []
        self._camera_draw_strokes: list[dict] = []
        self._camera_draw_active_stroke_points: list[tuple[float, float]] = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point: tuple[int, int] | None = None
        self._camera_draw_erasing = False
        self._drawing_grabbed_stroke_index: int | None = None
        self._drawing_grab_last_point: tuple[int, int] | None = None
        self._drawing_grab_history_pushed = False
        self._drawing_secondary_hand_reading = None
        self._drawing_stretch_active = False
        self._drawing_stretch_initial_distance: float | None = None
        self._drawing_stretch_initial_points: list[tuple[float, float]] | None = None
        self._drawing_stretch_centroid: tuple[float, float] | None = None
        self._drawing_stretch_history_pushed = False
        self._drawing_request_token = 0
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_request_action = ""
        self._drawing_shape_mode = False
        self._drawing_draw_grace_until = 0.0
        self._drawing_erase_grace_until = 0.0
        self._drawing_thumb_open_streak = 0
        self._drawing_wheel_candidate = "neutral"
        self._drawing_wheel_candidate_since = 0.0
        self._drawing_wheel_visible = False
        self._drawing_wheel_anchor = None
        self._drawing_wheel_selected_key: str | None = None
        self._drawing_wheel_selected_since = 0.0
        self._drawing_wheel_pose_grace_until = 0.0
        self._drawing_wheel_cooldown_until = 0.0
        self._drawing_wheel_cursor_offset: tuple[float, float] | None = None
        self.drawing_wheel_overlay = SpotifyWheelOverlay(config)
        self._utility_wheel_candidate = "neutral"
        self._utility_wheel_candidate_since = 0.0
        self._utility_wheel_visible = False
        self._utility_wheel_anchor = None
        self._utility_wheel_selected_key: str | None = None
        self._utility_wheel_selected_since = 0.0
        self._utility_wheel_pose_grace_until = 0.0
        self._utility_wheel_cooldown_until = 0.0
        self._utility_wheel_cursor_offset: tuple[float, float] | None = None
        self.utility_wheel_overlay = SpotifyWheelOverlay(config)
        self._utility_request_token = 0
        self._utility_request_action = ""
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm: tuple[float, float] | None = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._window_expand_candidate_since = 0.0
        self._window_contract_candidate_since = 0.0
        self._window_close_candidate_since = 0.0
        self._window_gesture_cooldown_until = 0.0
        self._window_pair_smoothed_distance: float | None = None
        self._window_pair_last_seen_at = 0.0
        self._window_pair_overlay = None
        self._window_sequence_start_state: str | None = None
        self._window_sequence_start_candidate: str | None = None
        self._window_sequence_start_candidate_since = 0.0
        self._window_sequence_target_candidate: str | None = None
        self._window_sequence_target_candidate_since = 0.0
        self._gestures_enabled = True
        self._selection_prompt_active = False
        self._selection_prompt_title = "Which file/folder?"
        self._selection_prompt_items: list[tuple[int, str, str]] = []
        self._selection_prompt_instruction = "Say the corresponding number."
        self._save_prompt_active = False
        self._save_prompt_text = "Where would you like to save this file?"

        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)

    def _record_action(self, label: str, display_text: str) -> None:
        if not label or label == "-":
            return
        if label.endswith("_failed") or label.endswith("_idle") or label.endswith("_requires_open") or label.endswith("_closed"):
            return
        undoable = label in _UNDO_LABEL_PAIRS
        event = ActionEvent(
            timestamp=time.time(),
            label=label,
            display_text=display_text or label,
            undoable=undoable,
        )
        with self._action_history_lock:
            self._action_history.append(event)
            snapshot = list(self._action_history)
        try:
            self.action_history_changed.emit(snapshot)
        except Exception:
            pass

    def undo_last_action(self) -> bool:
        with self._action_history_lock:
            target: ActionEvent | None = None
            for event in reversed(self._action_history):
                if event.is_undo:
                    continue
                if event.undoable:
                    target = event
                    break
        if target is None:
            return False
        inverse_label = _UNDO_LABEL_PAIRS.get(target.label)
        if inverse_label is None:
            return False
        performed = self._perform_action_by_label(inverse_label)
        if not performed:
            return False
        event = ActionEvent(
            timestamp=time.time(),
            label=inverse_label,
            display_text=f"undo: {target.display_text}",
            undoable=False,
            is_undo=True,
        )
        with self._action_history_lock:
            self._action_history.append(event)
            snapshot = list(self._action_history)
        try:
            self.action_history_changed.emit(snapshot)
        except Exception:
            pass
        return True

    def _perform_action_by_label(self, label: str) -> bool:
        try:
            if label == "spotify_next":
                return bool(self.spotify_controller.next_track())
            if label == "spotify_previous":
                return bool(self.spotify_controller.previous_track())
            if label == "spotify_toggle":
                return bool(self.spotify_controller.toggle_playback())
            if label == "spotify_shuffle":
                return bool(self.spotify_controller.toggle_shuffle())
            if label == "spotify_repeat":
                return bool(self.spotify_controller.toggle_repeat_track())
            if label == "youtube_next":
                return bool(self.youtube_controller.next_track())
            if label == "youtube_previous":
                return bool(self.youtube_controller.previous_track())
            if label == "youtube_toggle":
                return bool(self.youtube_controller.toggle_playback())
            if label == "chrome_back":
                return bool(self.chrome_controller.navigate_back())
            if label == "chrome_forward":
                return bool(self.chrome_controller.navigate_forward())
        except Exception:
            return False
        return False

    def action_history_snapshot(self) -> list[ActionEvent]:
        with self._action_history_lock:
            return list(self._action_history)

    def _prime_voice_app_hints_async(self) -> None:
        if self._app_hint_thread is not None and self._app_hint_thread.is_alive():
            return

        def _worker() -> None:
            try:
                self.voice_listener.set_app_hints(self.voice_processor.desktop_controller.application_hint_names())
            except Exception:
                pass

        self._app_hint_thread = threading.Thread(
            target=_worker,
            name="hgr-app-hints",
            daemon=True,
        )
        self._app_hint_thread.start()


    def _prime_voice_runtime_async(self) -> None:
        def _worker() -> None:
            try:
                self.voice_listener.prewarm()
            except Exception:
                pass

        threading.Thread(
            target=_worker,
            name="hgr-voice-prewarm",
            daemon=True,
        ).start()

    def _dictation_backend_label(self) -> str:
        def _pretty(name: str | None) -> str:
            if not name:
                return ""
            lower = name.lower()
            if lower == "cuda":
                return "CUDA"
            if lower == "vulkan":
                return "Vulkan"
            if lower == "cpu":
                return "CPU"
            return name.upper() if len(name) <= 4 else name.capitalize()

        dict_backend = _pretty(getattr(self.live_dictation_streamer, "backend", None))
        gram_backend = _pretty(getattr(self.llama_server, "backend", None)) if self.llama_server.available else ""
        if dict_backend and gram_backend and dict_backend != gram_backend:
            return f"Using {dict_backend} / {gram_backend}"
        if dict_backend:
            return f"Using {dict_backend}"
        if gram_backend:
            return f"Using {gram_backend}"
        return ""

    def _apply_grammar_correction(self, result: CorrectionResult) -> None:
        original = str(result.original or "")
        corrected = str(result.corrected or "")
        if not original or not corrected or original == corrected:
            return
        # Sanity guard against llama hallucinations / paraphrasing.
        # Allow legitimate de-duplication (corrections that strip stuttered
        # repeats) — those can drop ~half the chars/words and still be right.
        # Reject only if the result is obviously broken: empty, drastically
        # truncated, or wildly expanded.
        if not corrected.strip("\n\r\t .,;:!?'\""):
            print(f"[grammar] rejecting correction: empty/punctuation-only")
            return
        orig_len = len(original)
        corr_len = len(corrected)
        ratio = corr_len / orig_len if orig_len else 1.0
        orig_words = len([w for w in original.split() if w.strip()])
        corr_words = len([w for w in corrected.split() if w.strip()])
        word_delta = corr_words - orig_words
        if ratio < 0.4 or ratio > 1.6 or word_delta > 6:
            print(f"[grammar] rejecting correction: ratio={ratio:.2f} word_delta={word_delta:+d} (orig={orig_len}c/{orig_words}w corr={corr_len}c/{corr_words}w)")
            return
        with self._corrector_lock:
            if self.grammar_corrector.is_chunk_stale():
                print(f"[grammar] skipping apply: chunk stale after lock")
                return
            tail = self.grammar_corrector.snapshot_tail()
            previous = original + tail
            replacement = corrected + tail
            tic = self.text_input_controller
            try:
                target_hwnd = int(getattr(tic, "_target_hwnd", 0) or 0)
                foreground_hwnd = tic._foreground_window() if hasattr(tic, "_foreground_window") else 0
            except Exception:
                target_hwnd = 0
                foreground_hwnd = 0
            focus_ok = target_hwnd > 0 and target_hwnd == foreground_hwnd
            try:
                success = tic.replace_text(previous, replacement)
            except Exception as exc:
                print(f"[grammar] replace_text raised: {exc}")
                success = False
            if success:
                self._corrector_applied_message = "dictation corrected"
                print(f"[grammar] applied correction (-{len(previous)} +{len(replacement)} chars) focus_ok={focus_ok} target={target_hwnd} fg={foreground_hwnd}")
            else:
                self._corrector_applied_message = "correction skipped (focus lost)"
                print(f"[grammar] apply failed focus_ok={focus_ok} target={target_hwnd} fg={foreground_hwnd} msg={tic.message}")

    def _apply_refinement(self, result: RefinementResult) -> None:
        if not self._dictation_active:
            return
        refined_text = (result.text or "").strip()
        print(f"[refiner] received: {refined_text!r} ({result.duration_seconds:.2f}s)")
        if not refined_text:
            return
        refined_text = _strip_whisper_hallucinations(refined_text)
        if not refined_text:
            return
        with self._corrector_lock:
            state = self._dictation_state
            if state is None:
                return
            prev_text = str(state.get("last_final_text", "") or "")
            if not prev_text or state.get("last_final_refined"):
                return
            if state.get("committed"):
                # streaming hypothesis already typing the next utterance — skip
                return
            if state.get("stream_hyp_chars", 0):
                return
            typed_len = int(state.get("last_final_typed_len", 0))
            if typed_len <= 0 or typed_len > 600:
                return
            age = time.monotonic() - float(state.get("last_final_time", 0.0))
            if age > 8.0:
                return
            if refined_text == prev_text:
                state["last_final_refined"] = True
                return
            # safety: don't apply if the refined text is wildly larger than what
            # was streamed — usually means VAD captured multiple utterances and
            # we'd duplicate text in the editor.
            if len(refined_text) > max(60, int(len(prev_text) * 2.0)):
                print(f"[refiner] skipping: refined too large ({len(refined_text)} vs streamed {len(prev_text)})")
                state["last_final_refined"] = True
                return
            to_type = refined_text + " "
            tic = self.text_input_controller
            try:
                removed = tic.remove_text(typed_len)
            except Exception as exc:
                print(f"[refiner] remove_text raised: {exc}")
                return
            if not removed:
                return
            try:
                inserted = tic.insert_text(to_type)
            except Exception as exc:
                print(f"[refiner] insert_text raised: {exc}")
                return
            if not inserted:
                return
            try:
                self.grammar_corrector.sync_replace(typed_len, to_type)
            except Exception as exc:
                print(f"[refiner] sync_replace raised: {exc}")
            current_display = str(state.get("final_display", "") or "")
            if current_display.endswith(prev_text):
                current_display = current_display[: len(current_display) - len(prev_text)] + refined_text
            else:
                current_display = (current_display + " " + refined_text).strip() if current_display else refined_text
            state["final_display"] = current_display
            state["last_final_text"] = refined_text
            state["last_final_typed_len"] = len(to_type)
            state["last_final_refined"] = True
            print(f"[refiner] applied refinement: -{typed_len} +{len(to_type)} chars")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def gestures_enabled(self) -> bool:
        return bool(self._gestures_enabled)

    def set_gestures_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._gestures_enabled:
            return
        self._gestures_enabled = enabled
        if not enabled:
            self.mouse_controller.release_all()
            self.mouse_tracker.reset()
            self._last_mouse_update = self._blank_mouse_update()
            self._mouse_mode_enabled = False
            self._mouse_control_text = "gestures disabled"
            self._mouse_status_text = "off"
            self._reset_chrome_wheel(clear_cooldown=False)
            self._reset_spotify_wheel(clear_cooldown=False)
            self._reset_youtube_wheel(clear_cooldown=False)
            self._reset_drawing_wheel(clear_cooldown=False)
            self._reset_utility_wheel(clear_cooldown=False)
            self._reset_window_gesture_state(clear_cooldown=False)
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._camera_draw_last_point = None
            self._window_pair_smoothed_distance = None
            self._window_pair_overlay = None
            self._chrome_control_text = "gestures disabled"
            self._spotify_control_text = "gestures disabled"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._update_volume_overlay()
        else:
            self._chrome_control_text = self.chrome_controller.message
            self._spotify_control_text = self.spotify_controller.message
            if self.mouse_controller.available:
                self._mouse_control_text = "mouse mode off"
                self._mouse_status_text = "off"

    def _reset_drawing_runtime(self, *, keep_mode: bool = False) -> None:
        if not keep_mode:
            self._drawing_mode_enabled = False
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_cursor_norm = None
        self._drawing_tool = "hidden"
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_grabbed_stroke_index = None
        self._drawing_grab_last_point = None
        self._drawing_grab_history_pushed = False
        self._drawing_stretch_active = False
        self._drawing_stretch_initial_distance = None
        self._drawing_stretch_initial_points = None
        self._drawing_stretch_centroid = None
        self._drawing_stretch_history_pushed = False
        self._drawing_control_text = "drawing mode on" if self._drawing_mode_enabled else "drawing mode off"

    def _toggle_drawing_mode(self, now: float) -> None:
        self._drawing_mode_enabled = not self._drawing_mode_enabled
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_toggle_cooldown_until = now + 1.0
        self._drawing_cursor_norm = None
        self._drawing_tool = "hidden"
        self._camera_draw_last_point = None
        self._reset_drawing_wheel(clear_cooldown=False)
        self._reset_chrome_wheel(clear_cooldown=False)
        self._reset_spotify_wheel(clear_cooldown=False)
        self._reset_window_gesture_state(clear_cooldown=False)
        self.chrome_router.reset()
        self.spotify_router.reset()
        self.youtube_router.reset()
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._volume_mode_active = False
        self._volume_status_text = "paused"
        self._volume_overlay_visible = False
        self._update_volume_overlay()
        self.voice_status_overlay.hide_overlay()
        state = "enabled" if self._drawing_mode_enabled else "disabled"
        self._drawing_control_text = f"drawing mode {state}"
        try:
            self.voice_status_overlay.show_info_hint(
                "Draw mode: ON" if self._drawing_mode_enabled else "Draw mode: OFF",
                duration=3.0,
            )
        except Exception:
            pass
        try:
            self.command_detected.emit(self._drawing_control_text)
        except Exception:
            pass

    def _handle_drawing_toggle(self, prediction, hand_handedness: str | None, now: float) -> bool:
        left_pred = prediction if hand_handedness == "Left" else self._left_hand_prediction
        if left_pred is None:
            self._drawing_toggle_candidate_since = 0.0
            return False
        stable_label = str(getattr(left_pred, "stable_label", "neutral") or "neutral")
        if stable_label != "four":
            self._drawing_toggle_candidate_since = 0.0
            return False
        if now < self._drawing_toggle_cooldown_until:
            return True
        if self._drawing_toggle_candidate_since <= 0.0:
            self._drawing_toggle_candidate_since = now
            self._drawing_control_text = "hold left hand four for drawing mode"
            return True
        if now - self._drawing_toggle_candidate_since >= 0.6:
            self._toggle_drawing_mode(now)
            return True
        return True

    def _drawing_finger_extendedish(self, finger, *, primary: bool = False) -> bool:
        if finger is None:
            return False
        if finger.state in {"fully_open", "partially_curled"}:
            return True
        threshold = 0.50 if primary else 0.56
        return float(getattr(finger, "openness", 0.0) or 0.0) >= threshold

    def _drawing_finger_foldedish(self, finger) -> bool:
        if finger is None:
            return False
        if finger.state in {"closed", "mostly_curled"}:
            return True
        openness = float(getattr(finger, "openness", 0.0) or 0.0)
        curl = float(getattr(finger, "curl", 0.0) or 0.0)
        return openness <= 0.48 or curl >= 0.48

    def _drawing_thumb_foldedish(self, finger) -> bool:
        if finger is None:
            return False
        if finger.state in {"closed", "mostly_curled", "partially_curled"}:
            return True
        openness = float(getattr(finger, "openness", 0.0) or 0.0)
        curl = float(getattr(finger, "curl", 0.0) or 0.0)
        return openness <= 0.80 or curl >= 0.22

    def _drawing_draw_pose_active(self, prediction, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        outer_folded = sum(1 for name in ("middle", "ring", "pinky") if self._drawing_finger_foldedish(fingers.get(name)))
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_thumb_foldedish(fingers.get("thumb"))
            and outer_folded >= 2
            and float(getattr(fingers.get("middle"), "openness", 0.0) or 0.0) <= 0.60
        )

    def _drawing_erase_pose_active(self, prediction, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        spread = hand_reading.spreads.get("index_middle")
        spread_ok = False
        if spread is not None:
            spread_ok = spread.state == "together" or float(spread.distance) <= 0.60 or float(spread.together_strength) >= 0.14
        thumb_folded = self._drawing_thumb_foldedish(fingers.get("thumb"))
        ring_folded = self._drawing_finger_foldedish(fingers.get("ring"))
        pinky_folded = self._drawing_finger_foldedish(fingers.get("pinky"))
        folded_support = sum(1 for ok in (thumb_folded, ring_folded, pinky_folded) if ok)
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_finger_extendedish(fingers.get("middle"), primary=True)
            and folded_support >= 2
            and spread_ok
        )

    def _drawing_lift_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        thumb = fingers.get("thumb")
        if thumb is None or thumb.state not in {"fully_open", "partially_curled"} or thumb.openness < 0.56:
            return False
        extended = sum(
            1
            for name in ("index", "middle", "ring", "pinky")
            if self._drawing_finger_extendedish(fingers.get(name))
        )
        return extended >= 3

    def _drawing_pinch_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        spread = hand_reading.spreads.get("thumb_index")
        if spread is None:
            return False
        distance = float(spread.distance)
        together_strength = float(spread.together_strength)
        pinch_ok = (
            spread.state == "together"
            or distance <= 0.48
            or together_strength >= 0.22
        )
        tight_pinch = distance <= 0.30 or together_strength >= 0.40
        fingers = hand_reading.fingers
        index_curled = self._drawing_finger_foldedish(fingers.get("index"))
        middle_curled = self._drawing_finger_foldedish(fingers.get("middle"))
        ring_curled = self._drawing_finger_foldedish(fingers.get("ring"))
        pinky_curled = self._drawing_finger_foldedish(fingers.get("pinky"))
        thumb = fingers.get("thumb")
        thumb_open = False
        if thumb is not None:
            thumb_open = thumb.state in {"fully_open", "partially_curled"} and float(getattr(thumb, "openness", 0.0) or 0.0) >= 0.45
        claw_shape = thumb_open and index_curled and middle_curled and ring_curled and pinky_curled
        if claw_shape:
            return True
        if tight_pinch and thumb_open and index_curled:
            return True
        if not pinch_ok:
            return False
        index_extended = self._drawing_finger_extendedish(fingers.get("index"), primary=True)
        if index_extended:
            return False
        outer_folded = sum(1 for curled in (ring_curled, pinky_curled) if curled)
        return outer_folded >= 1

    def _drawing_wheel_pose_active(self, prediction) -> bool:
        if prediction is None:
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        return stable_label == "wheel_pose" or (raw_label == "wheel_pose" and confidence >= 0.52)

    def _update_drawing_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        if not self._drawing_mode_enabled:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            return
        if hand_handedness != "Right" or hand_reading is None:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._drawing_control_text = f"drawing mode enabled ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        try:
            cursor_x = max(0.0, min(1.0, float(hand_reading.landmarks[8][0])))
            cursor_y = max(0.0, min(1.0, float(hand_reading.landmarks[8][1])))
        except Exception:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._camera_draw_last_point = None
            return
        self._drawing_cursor_norm = (cursor_x, cursor_y)
        if self._drawing_lift_pose_active(hand_reading):
            self._drawing_tool = "hover"
            self._drawing_control_text = f"drawing hover ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        erase_active = self._drawing_erase_pose_active(prediction, hand_reading)
        draw_active = self._drawing_draw_pose_active(prediction, hand_reading)
        if erase_active:
            self._drawing_erase_grace_until = now + 0.25
        if draw_active:
            self._drawing_draw_grace_until = now + 0.25

        # Fast-stop: two consecutive frames of a decisively open thumb cancel the
        # draw/erase grace so the pen lifts immediately when the user opens their
        # thumb. The streak (not a single-frame check) keeps rotation wobble —
        # where the thumb briefly reads as open or partially curled — from ending
        # a stroke early.
        thumb = hand_reading.fingers.get("thumb")
        thumb_decisive_open = (
            thumb is not None
            and getattr(thumb, "state", None) == "fully_open"
            and float(getattr(thumb, "openness", 0.0) or 0.0) >= 0.88
        )
        if thumb_decisive_open and not draw_active and not erase_active:
            self._drawing_thumb_open_streak += 1
        else:
            self._drawing_thumb_open_streak = 0
        if self._drawing_thumb_open_streak >= 2:
            if self._drawing_render_target == "camera" and self._camera_draw_active_stroke_points:
                self._commit_camera_draw_stroke()
            self._drawing_draw_grace_until = 0.0
            self._drawing_erase_grace_until = 0.0

        if erase_active or now < self._drawing_erase_grace_until:
            self._drawing_tool = "erase"
            self._drawing_control_text = f"drawing erase ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        if draw_active or now < self._drawing_draw_grace_until:
            self._drawing_tool = "draw"
            self._drawing_control_text = f"drawing ({self._drawing_render_target})"
            return
        if self._drawing_pinch_pose_active(hand_reading):
            self._drawing_tool = "grab"
            self._drawing_control_text = f"drawing grab ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        self._drawing_tool = "hover"

        self._drawing_control_text = f"drawing hover ({self._drawing_render_target})"
        self._camera_draw_last_point = None

    def _reset_drawing_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._drawing_wheel_visible = False
        self._drawing_wheel_anchor = None
        self._drawing_wheel_cursor_offset = None
        self._drawing_wheel_selected_key = None
        self._drawing_wheel_selected_since = 0.0
        self._drawing_wheel_pose_grace_until = 0.0
        self._drawing_wheel_candidate = "neutral"
        self._drawing_wheel_candidate_since = 0.0
        if clear_cooldown:
            self._drawing_wheel_cooldown_until = 0.0
        if self.drawing_wheel_overlay.isVisible():
            self.drawing_wheel_overlay.hide_overlay()

    def _drawing_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        return (
            ("switch_view", "Switch View", 90.0),
            ("save", "Save Drawing", 18.0),
            ("shape", "Shape Mode", 306.0),
            ("pen_options", "Pen Options", 234.0),
            ("eraser_options", "Eraser Options", 162.0),
        )

    def _drawing_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._drawing_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _queue_drawing_request(self, action: str) -> None:
        self._drawing_request_token += 1
        self._drawing_request_action = str(action or "")

    def _execute_drawing_wheel_action(self, key: str) -> None:
        if key == "switch_view":
            if self._drawing_render_target == "camera":
                self._commit_camera_draw_stroke()
                self._camera_draw_last_point = None
                self._camera_draw_erasing = False
            self._drawing_render_target = "camera" if self._drawing_render_target == "screen" else "screen"
            self._drawing_control_text = f"drawing target {self._drawing_render_target}"
            self.command_detected.emit(self._drawing_control_text)
            return
        if key == "pen_options":
            self._queue_drawing_request("pen_options")
            self._drawing_control_text = "drawing pen options"
            self.command_detected.emit("Drawing pen options")
            return
        if key == "eraser_options":
            self._queue_drawing_request("eraser_options")
            self._drawing_control_text = "drawing eraser options"
            self.command_detected.emit("Drawing eraser options")
            return
        if key == "save":
            self._queue_drawing_request("save")
            self._drawing_control_text = "drawing save"
            self.command_detected.emit("Saving drawing")
            return
        if key == "shape":
            self._drawing_shape_mode = not self._drawing_shape_mode
            self._queue_drawing_request("shape_on" if self._drawing_shape_mode else "shape_off")
            self._drawing_control_text = "shape mode on" if self._drawing_shape_mode else "shape mode off"
            hint_text = "Shape mode: ON" if self._drawing_shape_mode else "Shape mode: OFF"
            try:
                self.voice_status_overlay.show_info_hint(hint_text, duration=3.0)
            except Exception:
                pass
            self.command_detected.emit(self._drawing_control_text)
            return
        self._drawing_control_text = "drawing wheel action"
        self.command_detected.emit(self._drawing_control_text)

    def _update_drawing_wheel_selection(self, hand_reading, now: float) -> None:
        if self._drawing_wheel_anchor is None:
            self._drawing_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._drawing_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._drawing_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._drawing_wheel_items())
        if selection_key is None:
            self._drawing_wheel_selected_key = None
            self._drawing_wheel_selected_since = now
            self._drawing_control_text = "drawing wheel active"
            return
        if selection_key != self._drawing_wheel_selected_key:
            self._drawing_wheel_selected_key = selection_key
            self._drawing_wheel_selected_since = now
            self._drawing_control_text = f"drawing wheel: {self._drawing_wheel_label(selection_key)}"
            return
        if now - self._drawing_wheel_selected_since < 0.85:
            return
        self._execute_drawing_wheel_action(selection_key)
        self._drawing_wheel_cooldown_until = now + 1.5
        self._reset_drawing_wheel()

    def _update_drawing_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._drawing_wheel_visible and now >= self._drawing_wheel_pose_grace_until:
                self._drawing_control_text = "drawing wheel closed"
                self._reset_drawing_wheel()
            else:
                self._drawing_wheel_candidate = "neutral"
                self._drawing_wheel_candidate_since = now
            return self._drawing_wheel_visible
        wheel_pose = self._drawing_wheel_pose_active(prediction)
        if self._drawing_wheel_visible:
            if wheel_pose:
                self._drawing_wheel_pose_grace_until = now + 0.25
                self._update_drawing_wheel_selection(hand_reading, now)
            elif now >= self._drawing_wheel_pose_grace_until:
                self._drawing_control_text = "drawing wheel closed"
                self._reset_drawing_wheel()
            return True
        if now < self._drawing_wheel_cooldown_until:
            if not wheel_pose:
                self._drawing_wheel_candidate = "neutral"
            return False
        if not wheel_pose:
            self._drawing_wheel_candidate = "neutral"
            self._drawing_wheel_candidate_since = now
            return False
        if self._drawing_wheel_candidate != "wheel_pose":
            self._drawing_wheel_candidate = "wheel_pose"
            self._drawing_wheel_candidate_since = now
            self._drawing_control_text = "hold wheel pose for drawing settings"
            return True
        if now - self._drawing_wheel_candidate_since < 0.85:
            return True
        self._drawing_wheel_visible = True
        self._drawing_wheel_anchor = hand_reading.palm.center.copy()
        self._drawing_wheel_cursor_offset = (0.0, 0.0)
        self._drawing_wheel_selected_key = None
        self._drawing_wheel_selected_since = now
        self._drawing_wheel_pose_grace_until = now + 0.25
        self._drawing_control_text = "drawing wheel active"
        return True

    def save_camera_draw_snapshot(self, target_path) -> bool:
        """Write the current camera-target drawing canvas to a PNG.

        Called from the UI thread when the save wheel action fires while the
        drawing render target is ``camera``. The screen overlay is empty in
        that case, so without this the save would produce a blank PNG.
        """
        canvas = self._camera_draw_canvas
        if canvas is None:
            return False
        if self._camera_draw_active_stroke_points:
            self._commit_camera_draw_stroke()
        try:
            snapshot = canvas.copy()
        except Exception:
            return False
        try:
            height, width = snapshot.shape[:2]
            alpha = snapshot[:, :, 3:4].astype(np.float32) / 255.0
            fg = snapshot[:, :, :3].astype(np.float32)
            # Pick a background that contrasts with the average stroke color —
            # mirrors the screen-overlay save so dark pens on dark bg stay readable.
            mean_stroke_bgr = fg.sum(axis=(0, 1)) / max(float(alpha.sum()) * 3.0, 1.0)
            bg_color = (255.0, 255.0, 255.0) if float(mean_stroke_bgr.mean()) < 60.0 else (0.0, 0.0, 0.0)
            bg = np.zeros((height, width, 3), dtype=np.float32)
            bg[:, :, 0] = bg_color[0]
            bg[:, :, 1] = bg_color[1]
            bg[:, :, 2] = bg_color[2]
            composite = (fg * alpha + bg * (1.0 - alpha)).clip(0.0, 255.0).astype(np.uint8)
            from pathlib import Path as _Path
            path = _Path(str(target_path))
            path.parent.mkdir(parents=True, exist_ok=True)
            return bool(cv2.imwrite(str(path), composite))
        except Exception:
            return False

    def acknowledge_drawing_request(self, token: int | None = None) -> None:
        try:
            current_token = int(self._drawing_request_token)
        except Exception:
            current_token = 0
        if token is not None:
            try:
                requested_token = int(token)
            except Exception:
                requested_token = -1
            if requested_token != current_token:
                return
        self._drawing_request_action = ""

    def _reset_utility_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._utility_wheel_candidate = "neutral"
        self._utility_wheel_candidate_since = 0.0
        self._utility_wheel_visible = False
        self._utility_wheel_anchor = None
        self._utility_wheel_selected_key = None
        self._utility_wheel_selected_since = 0.0
        self._utility_wheel_pose_grace_until = 0.0
        self._utility_wheel_cursor_offset = None
        if clear_cooldown:
            self._utility_wheel_cooldown_until = 0.0
        if self.utility_wheel_overlay.isVisible():
            self.utility_wheel_overlay.hide_overlay()

    def _utility_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        labels = (
            ("screenshot", "Screenshot"),
            ("screenshot_custom", "Screenshot Custom"),
            ("screen_record", "Screen Record"),
            ("screen_record_custom", "Screen Record Custom"),
            ("clip_30s", "Clip 30 Sec"),
            ("clip_1m", "Clip 1 Min"),
        )
        slice_span = 360.0 / len(labels)
        return tuple(
            (key, label, (90.0 - index * slice_span) % 360.0)
            for index, (key, label) in enumerate(labels)
        )

    def _utility_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._utility_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _utility_wheel_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_finger_extendedish(fingers.get("pinky"))
            and self._drawing_finger_foldedish(fingers.get("thumb"))
            and self._drawing_finger_foldedish(fingers.get("middle"))
            and self._drawing_finger_foldedish(fingers.get("ring"))
        )

    def _queue_utility_request(self, action: str) -> None:
        self._utility_request_token += 1
        self._utility_request_action = str(action or "")

    def acknowledge_utility_request(self, token: int | None = None) -> None:
        try:
            current_token = int(self._utility_request_token)
        except Exception:
            current_token = 0
        if token is not None:
            try:
                requested_token = int(token)
            except Exception:
                requested_token = -1
            if requested_token != current_token:
                return
        self._utility_request_action = ""

    def _execute_utility_wheel_action(self, key: str) -> None:
        if key == "screenshot":
            self._queue_utility_request("screenshot_full")
            self.command_detected.emit("Full screenshot in 3 seconds")
            return
        if key == "screenshot_custom":
            self._queue_utility_request("screenshot_custom")
            self.command_detected.emit("Drag to choose screenshot area")
            return
        if key == "screen_record":
            self._queue_utility_request("record_full")
            self.command_detected.emit("Full screen record in 3 seconds")
            return
        if key == "screen_record_custom":
            self._queue_utility_request("record_custom")
            self.command_detected.emit("Drag to choose recording area")
            return
        if key == "clip_30s":
            self._queue_utility_request("clip_30s")
            self.command_detected.emit("Saving last 30 seconds")
            return
        if key == "clip_1m":
            self._queue_utility_request("clip_1m")
            self.command_detected.emit("Saving last 1 minute")
            return
        self.command_detected.emit(f"{self._utility_wheel_label(key)} coming soon")

    def _update_utility_wheel_selection(self, hand_reading, now: float) -> None:
        if self._utility_wheel_anchor is None:
            self._utility_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._utility_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._utility_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._utility_wheel_items())
        if selection_key is None:
            self._utility_wheel_selected_key = None
            self._utility_wheel_selected_since = now
            return
        if selection_key != self._utility_wheel_selected_key:
            self._utility_wheel_selected_key = selection_key
            self._utility_wheel_selected_since = now
            return
        if now - self._utility_wheel_selected_since < 0.85:
            return
        self._execute_utility_wheel_action(selection_key)
        self._utility_wheel_cooldown_until = now + 1.5
        self._reset_utility_wheel()

    def _update_utility_wheel(self, hand_reading, hand_handedness: str | None, now: float) -> bool:
        active = hand_handedness == "Right" and hand_reading is not None
        if not active:
            if self._utility_wheel_visible and now >= self._utility_wheel_pose_grace_until:
                self._reset_utility_wheel()
            else:
                self._utility_wheel_candidate = "neutral"
                self._utility_wheel_candidate_since = now
            return self._utility_wheel_visible
        wheel_pose = self._utility_wheel_pose_active(hand_reading)
        if self._utility_wheel_visible:
            if wheel_pose:
                self._utility_wheel_pose_grace_until = now + 0.25
                self._update_utility_wheel_selection(hand_reading, now)
            elif now >= self._utility_wheel_pose_grace_until:
                self._reset_utility_wheel()
            return True
        if now < self._utility_wheel_cooldown_until:
            if not wheel_pose:
                self._utility_wheel_candidate = "neutral"
            return False
        if not wheel_pose:
            self._utility_wheel_candidate = "neutral"
            self._utility_wheel_candidate_since = now
            return False
        if self._utility_wheel_candidate != "utility_wheel_pose":
            self._utility_wheel_candidate = "utility_wheel_pose"
            self._utility_wheel_candidate_since = now
            return True
        if now - self._utility_wheel_candidate_since < 0.85:
            return True
        self._utility_wheel_visible = True
        self._utility_wheel_anchor = hand_reading.palm.center.copy()
        self._utility_wheel_cursor_offset = (0.0, 0.0)
        self._utility_wheel_selected_key = None
        self._utility_wheel_selected_since = now
        self._utility_wheel_pose_grace_until = now + 0.25
        return True

    def set_utility_recording_active(self, active: bool) -> None:
        self._utility_recording_active = bool(active)
        if not self._utility_recording_active:
            self._utility_recording_stop_candidate_since = 0.0

    def set_utility_capture_selection_active(self, active: bool) -> None:
        self._utility_capture_selection_active = bool(active)
        self._utility_capture_clicks_armed = False
        if not self._utility_capture_selection_active:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False

    def _utility_capture_click_down(self, finger) -> bool:
        if finger is None:
            return False
        openness = float(getattr(finger, 'openness', 0.0) or 0.0)
        curl = float(getattr(finger, 'curl', 0.0) or 0.0)
        return finger.state in {'closed', 'mostly_curled'} or openness <= 0.42 or curl >= 0.52

    def _update_utility_capture_selection(self, hand_reading, hand_handedness: str | None) -> None:
        if hand_handedness != 'Right' or hand_reading is None:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            self._utility_capture_clicks_armed = False
            return
        try:
            palm_center = getattr(hand_reading.palm, 'center', None)
            if palm_center is None or len(palm_center) < 2:
                raise ValueError('missing palm center')
            cursor_x = max(0.0, min(1.0, float(palm_center[0])))
            cursor_y = max(0.0, min(1.0, float(palm_center[1])))
        except Exception:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            self._utility_capture_clicks_armed = False
            return
        fingers = hand_reading.fingers
        self._utility_capture_cursor_norm = (cursor_x, cursor_y)
        index_down = self._utility_capture_click_down(fingers.get('index'))
        middle_down = self._utility_capture_click_down(fingers.get('middle'))
        if not self._utility_capture_clicks_armed:
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            if not index_down and not middle_down:
                self._utility_capture_clicks_armed = True
            return
        self._utility_capture_left_down = index_down and not middle_down
        self._utility_capture_right_down = middle_down and not index_down

    def set_drawing_brush(self, color: str | None = None, thickness: int | None = None) -> None:
        if color:
            self._drawing_brush_hex = str(color)
        if thickness is not None:
            self._drawing_brush_thickness = int(max(2, thickness))

    def set_drawing_eraser(self, thickness: int | None = None, mode: str | None = None) -> None:
        if thickness is not None:
            self._drawing_eraser_thickness = int(max(4, thickness))
        if mode:
            normalized = str(mode).strip().lower()
            if normalized in {"normal", "stroke"}:
                self._drawing_eraser_mode = normalized

    def _drawing_brush_bgr(self) -> tuple[int, int, int]:
        value = str(self._drawing_brush_hex or "#FFFFFF").strip().lstrip("#")
        if len(value) != 6:
            return (255, 255, 255)
        try:
            r = int(value[0:2], 16); g = int(value[2:4], 16); b = int(value[4:6], 16)
        except Exception:
            return (255, 255, 255)
        return (b, g, r)

    def _ensure_camera_draw_canvas(self, frame_shape) -> None:
        height, width = frame_shape[:2]
        if height <= 0 or width <= 0:
            return
        if self._camera_draw_canvas is not None and self._camera_draw_canvas.shape[:2] == (height, width):
            return
        self._camera_draw_canvas = np.zeros((height, width, 4), dtype=np.uint8)
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False

    def _clone_camera_draw_strokes(self) -> list[dict]:
        clones: list[dict] = []
        for stroke in self._camera_draw_strokes:
            clones.append(
                {
                    "color": tuple(int(v) for v in stroke.get("color", (255, 255, 255))),
                    "thickness": int(stroke.get("thickness", 2)),
                    "points": [(float(x), float(y)) for x, y in stroke.get("points", [])],
                }
            )
        return clones

    def _camera_draw_rerender_from_strokes(self) -> None:
        if self._camera_draw_canvas is None:
            return
        self._camera_draw_canvas[:, :, :] = 0
        for stroke in self._camera_draw_strokes:
            points = stroke.get("points") or []
            if len(points) < 2:
                continue
            color = tuple(int(v) for v in stroke.get("color", (255, 255, 255)))
            thickness = int(max(1, stroke.get("thickness", 2)))
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                cv2.line(
                    self._camera_draw_canvas,
                    (int(round(x1)), int(round(y1))),
                    (int(round(x2)), int(round(y2))),
                    (*color, 255),
                    thickness,
                    cv2.LINE_AA,
                )

    @staticmethod
    def _camera_point_to_segment_distance_sq(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        abx = bx - ax
        aby = by - ay
        if abs(abx) < 1e-9 and abs(aby) < 1e-9:
            dx = px - ax
            dy = py - ay
            return dx * dx + dy * dy
        apx = px - ax
        apy = py - ay
        denom = abx * abx + aby * aby
        t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
        cx = ax + t * abx
        cy = ay + t * aby
        dx = px - cx
        dy = py - cy
        return dx * dx + dy * dy

    def _camera_draw_find_stroke_at(self, px: float, py: float, radius: float) -> int | None:
        for idx in range(len(self._camera_draw_strokes) - 1, -1, -1):
            if self._camera_draw_stroke_hits_position(self._camera_draw_strokes[idx], px, py, radius):
                return idx
        return None

    def _handle_drawing_grab(self, point: tuple[int, int]) -> None:
        px = float(point[0])
        py = float(point[1])
        if self._drawing_grabbed_stroke_index is None:
            radius = max(12.0, float(self._drawing_brush_thickness) * 1.2)
            hit = self._camera_draw_find_stroke_at(px, py, radius)
            if hit is None:
                self._drawing_grab_last_point = point
                return
            if self._camera_draw_raster_dirty and self._camera_draw_strokes:
                self._camera_draw_raster_dirty = False
                self._camera_draw_rerender_from_strokes()
            if not self._drawing_grab_history_pushed:
                self._camera_draw_push_history()
                self._drawing_grab_history_pushed = True
            self._drawing_grabbed_stroke_index = hit
            self._drawing_grab_last_point = point
            return
        last = self._drawing_grab_last_point
        if last is None:
            self._drawing_grab_last_point = point
            return
        dx = point[0] - last[0]
        dy = point[1] - last[1]
        self._drawing_grab_last_point = point
        if dx == 0 and dy == 0:
            return
        idx = self._drawing_grabbed_stroke_index
        if idx is None or idx < 0 or idx >= len(self._camera_draw_strokes):
            self._release_drawing_grab()
            return
        stroke = self._camera_draw_strokes[idx]
        points = stroke.get("points") or []
        stroke["points"] = [(float(x) + dx, float(y) + dy) for x, y in points]
        self._camera_draw_rerender_from_strokes()

    def _camera_draw_stroke_hits_position(self, stroke: dict, px: float, py: float, radius: float) -> bool:
        points = stroke.get("points") or []
        if not points:
            return False
        threshold = max(float(radius), float(stroke.get("thickness", 0)) * 0.5 + 2.0)
        limit_sq = threshold * threshold
        if len(points) == 1:
            sx, sy = points[0]
            dx = sx - px
            dy = sy - py
            return dx * dx + dy * dy <= limit_sq
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if self._camera_point_to_segment_distance_sq(px, py, float(ax), float(ay), float(bx), float(by)) <= limit_sq:
                return True
        return False

    def _commit_camera_draw_stroke(self) -> None:
        if not self._camera_draw_active_stroke_points:
            return
        points = list(self._camera_draw_active_stroke_points)
        if len(points) == 1:
            x, y = points[0]
            points.append((x + 0.01, y + 0.01))
        self._camera_draw_strokes.append(
            {
                "color": tuple(int(v) for v in self._drawing_brush_bgr()),
                "thickness": int(max(2, self._drawing_brush_thickness)),
                "points": points,
            }
        )
        self._camera_draw_active_stroke_points = []

    def _camera_draw_point(self, frame_shape) -> tuple[int, int] | None:
        if self._drawing_cursor_norm is None:
            return None
        height, width = frame_shape[:2]
        x = int(round(max(0.0, min(1.0, float(self._drawing_cursor_norm[0]))) * max(width - 1, 1)))
        y = int(round(max(0.0, min(1.0, float(self._drawing_cursor_norm[1]))) * max(height - 1, 1)))
        return x, y

    def _camera_draw_push_history(self) -> None:
        if self._camera_draw_canvas is None:
            return
        self._camera_draw_history.append(
            (
                self._camera_draw_canvas.copy(),
                self._clone_camera_draw_strokes(),
                bool(self._camera_draw_raster_dirty),
            )
        )
        if len(self._camera_draw_history) > 24:
            self._camera_draw_history = self._camera_draw_history[-24:]

    def _camera_draw_undo(self) -> bool:
        if not self._camera_draw_history:
            return False
        canvas, strokes, raster_dirty = self._camera_draw_history.pop()
        self._camera_draw_canvas = canvas
        self._camera_draw_strokes = strokes
        self._camera_draw_raster_dirty = bool(raster_dirty)
        self._camera_draw_active_stroke_points = []
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        return True

    def _perform_drawing_swipe_action(self, direction: str) -> bool:
        key = str(direction or "").strip().lower()
        if key not in {"swipe_left", "swipe_right"}:
            return False
        if self._drawing_render_target == "camera":
            self._ensure_camera_draw_canvas((480, 640, 3))
            if key == "swipe_left":
                success = self._camera_draw_undo()
                self._drawing_control_text = "drawing undo" if success else "drawing nothing to undo"
            else:
                if self._camera_draw_canvas is not None:
                    self._camera_draw_push_history()
                    self._camera_draw_canvas[:, :, :] = 0
                    self._camera_draw_strokes = []
                    self._camera_draw_active_stroke_points = []
                    self._camera_draw_raster_dirty = False
                    self._camera_draw_last_point = None
                success = True
                self._drawing_control_text = "drawing cleared"
            self.command_detected.emit(self._drawing_control_text)
            return success
        self._queue_drawing_request("undo" if key == "swipe_left" else "clear")
        self._drawing_control_text = "drawing undo" if key == "swipe_left" else "drawing cleared"
        self.command_detected.emit(self._drawing_control_text)
        return True

    def _release_drawing_grab(self) -> None:
        self._drawing_grabbed_stroke_index = None
        self._drawing_grab_last_point = None
        self._drawing_grab_history_pushed = False
        self._release_drawing_stretch()

    def _release_drawing_stretch(self) -> None:
        self._drawing_stretch_active = False
        self._drawing_stretch_initial_distance = None
        self._drawing_stretch_initial_points = None
        self._drawing_stretch_centroid = None
        self._drawing_stretch_history_pushed = False

    def _drawing_secondary_pinch_point(self, frame_shape) -> tuple[int, int] | None:
        secondary = self._drawing_secondary_hand_reading
        if secondary is None:
            return None
        if not self._drawing_pinch_pose_active(secondary):
            return None
        try:
            cx = max(0.0, min(1.0, float(secondary.landmarks[8][0])))
            cy = max(0.0, min(1.0, float(secondary.landmarks[8][1])))
        except Exception:
            return None
        height, width = frame_shape[:2]
        x = int(round(cx * max(width - 1, 1)))
        y = int(round(cy * max(height - 1, 1)))
        return x, y

    def _handle_drawing_stretch(self, primary_point: tuple[int, int], secondary_point: tuple[int, int]) -> None:
        idx = self._drawing_grabbed_stroke_index
        if idx is None or idx < 0 or idx >= len(self._camera_draw_strokes):
            self._release_drawing_grab()
            return
        stroke = self._camera_draw_strokes[idx]
        dx = float(secondary_point[0] - primary_point[0])
        dy = float(secondary_point[1] - primary_point[1])
        distance = (dx * dx + dy * dy) ** 0.5
        if distance < 6.0:
            return
        if not self._drawing_stretch_active:
            points = [(float(x), float(y)) for x, y in (stroke.get("points") or [])]
            if not points:
                return
            if self._camera_draw_raster_dirty and self._camera_draw_strokes:
                self._camera_draw_raster_dirty = False
                self._camera_draw_rerender_from_strokes()
            if not self._drawing_stretch_history_pushed and not self._drawing_grab_history_pushed:
                self._camera_draw_push_history()
                self._drawing_stretch_history_pushed = True
            cx = sum(p[0] for p in points) / len(points)
            cy = sum(p[1] for p in points) / len(points)
            self._drawing_stretch_initial_points = points
            self._drawing_stretch_initial_distance = distance
            self._drawing_stretch_centroid = (cx, cy)
            self._drawing_stretch_active = True
            return
        initial_distance = self._drawing_stretch_initial_distance or 0.0
        initial_points = self._drawing_stretch_initial_points
        centroid = self._drawing_stretch_centroid
        if initial_distance <= 0.0 or initial_points is None or centroid is None:
            return
        scale = distance / initial_distance
        scale = max(0.2, min(5.0, scale))
        cx, cy = centroid
        stroke["points"] = [
            (cx + (x - cx) * scale, cy + (y - cy) * scale)
            for x, y in initial_points
        ]
        base_thickness = stroke.get("_base_thickness")
        if base_thickness is None:
            base_thickness = int(stroke.get("thickness", 2))
            stroke["_base_thickness"] = base_thickness
        stroke["thickness"] = int(max(1, round(base_thickness * scale)))
        self._camera_draw_rerender_from_strokes()

    def _update_camera_drawing_canvas(self, frame_shape) -> None:
        if self._drawing_render_target != "camera":
            self._commit_camera_draw_stroke()
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False
            self._release_drawing_grab()
            return
        self._ensure_camera_draw_canvas(frame_shape)
        if self._camera_draw_canvas is None:
            return
        point = self._camera_draw_point(frame_shape)
        if point is None:
            self._commit_camera_draw_stroke()
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False
            self._release_drawing_grab()
            return
        if self._drawing_tool == "grab":
            self._commit_camera_draw_stroke()
            self._camera_draw_erasing = False
            self._camera_draw_last_point = None
            secondary_point = self._drawing_secondary_pinch_point(frame_shape)
            stretch_candidate = False
            if (
                secondary_point is not None
                and self._drawing_grabbed_stroke_index is not None
                and 0 <= self._drawing_grabbed_stroke_index < len(self._camera_draw_strokes)
            ):
                stroke = self._camera_draw_strokes[self._drawing_grabbed_stroke_index]
                radius = max(18.0, float(stroke.get("thickness", 0)) * 1.4 + 8.0)
                stretch_candidate = self._camera_draw_stroke_hits_position(stroke, float(secondary_point[0]), float(secondary_point[1]), radius)
            if stretch_candidate and secondary_point is not None:
                self._drawing_grab_last_point = point
                self._handle_drawing_stretch(point, secondary_point)
                return
            if self._drawing_stretch_active:
                self._release_drawing_stretch()
            self._handle_drawing_grab(point)
            return
        if self._drawing_grabbed_stroke_index is not None:
            self._release_drawing_grab()
        brush = self._drawing_brush_bgr()
        thickness = int(max(2, self._drawing_brush_thickness))
        if self._drawing_tool == "draw":
            self._camera_draw_erasing = False
            if self._camera_draw_last_point is None:
                self._camera_draw_push_history()
                self._camera_draw_last_point = point
                self._camera_draw_active_stroke_points = [(float(point[0]), float(point[1]))]
            cv2.line(self._camera_draw_canvas, self._camera_draw_last_point, point, (*brush, 255), thickness, cv2.LINE_AA)
            self._camera_draw_active_stroke_points.append((float(point[0]), float(point[1])))
            self._camera_draw_last_point = point
            return
        self._commit_camera_draw_stroke()
        if self._drawing_tool == "erase":
            radius = max(10, int(max(4, self._drawing_eraser_thickness) * 1.35))
            if self._drawing_eraser_mode == "stroke":
                if self._camera_draw_raster_dirty and self._camera_draw_strokes:
                    self._camera_draw_raster_dirty = False
                    self._camera_draw_rerender_from_strokes()
                if not self._camera_draw_erasing:
                    self._camera_draw_push_history()
                    self._camera_draw_erasing = True
                px = float(point[0])
                py = float(point[1])
                hit_index = None
                for idx in range(len(self._camera_draw_strokes) - 1, -1, -1):
                    if self._camera_draw_stroke_hits_position(self._camera_draw_strokes[idx], px, py, float(radius)):
                        hit_index = idx
                        break
                if hit_index is not None:
                    self._camera_draw_strokes.pop(hit_index)
                    self._camera_draw_rerender_from_strokes()
            else:
                if not self._camera_draw_erasing:
                    self._camera_draw_push_history()
                    self._camera_draw_erasing = True
                cv2.circle(self._camera_draw_canvas, point, radius, (0, 0, 0, 0), thickness=-1, lineType=cv2.LINE_AA)
                self._camera_draw_raster_dirty = True
            self._camera_draw_last_point = None
        else:
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False

    def _blend_camera_drawing_overlay(self, frame) -> None:
        if self._drawing_render_target != "camera" or self._camera_draw_canvas is None:
            return
        alpha = self._camera_draw_canvas[:, :, 3:4].astype(np.float32) / 255.0
        if float(alpha.max()) > 0.0:
            overlay_rgb = self._camera_draw_canvas[:, :, :3].astype(np.float32)
            frame[:] = np.clip(frame.astype(np.float32) * (1.0 - alpha) + overlay_rgb * alpha, 0.0, 255.0).astype(np.uint8)
        point = self._camera_draw_point(frame.shape)
        if point is None or self._drawing_tool == "hidden":
            return
        radius = max(6, int(self._drawing_brush_thickness))
        if self._drawing_tool == "draw":
            cv2.circle(frame, point, radius, self._drawing_brush_bgr(), thickness=-1, lineType=cv2.LINE_AA)
            cv2.circle(frame, point, radius + 2, (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)
        elif self._drawing_tool == "erase":
            cv2.circle(frame, point, max(8, int(radius * 1.5)), (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)
        elif self._drawing_tool == "grab":
            grab_color = (30, 220, 255) if self._drawing_grabbed_stroke_index is not None else (200, 200, 200)
            cv2.circle(frame, point, radius + 4, grab_color, thickness=2, lineType=cv2.LINE_AA)
            cv2.circle(frame, point, max(3, radius // 2), grab_color, thickness=-1, lineType=cv2.LINE_AA)
            secondary_point = self._drawing_secondary_pinch_point(frame.shape)
            if secondary_point is not None:
                stretch_color = (255, 140, 30) if self._drawing_stretch_active else (160, 200, 240)
                cv2.circle(frame, secondary_point, radius + 4, stretch_color, thickness=2, lineType=cv2.LINE_AA)
                cv2.circle(frame, secondary_point, max(3, radius // 2), stretch_color, thickness=-1, lineType=cv2.LINE_AA)
                if self._drawing_stretch_active:
                    cv2.line(frame, point, secondary_point, stretch_color, thickness=1, lineType=cv2.LINE_AA)
        else:
            cv2.circle(frame, point, radius, (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)

    def _reset_window_gesture_state(self, *, clear_cooldown: bool = False) -> None:
        self._window_expand_candidate_since = 0.0
        self._window_contract_candidate_since = 0.0
        self._window_close_candidate_since = 0.0
        self._window_pair_smoothed_distance = None
        self._window_pair_last_seen_at = 0.0
        self._window_pair_overlay = None
        self._window_sequence_start_state = None
        self._window_sequence_start_candidate = None
        self._window_sequence_start_candidate_since = 0.0
        self._window_sequence_target_candidate = None
        self._window_sequence_target_candidate_since = 0.0
        if clear_cooldown:
            self._window_gesture_cooldown_until = 0.0

    def _window_pair_pose_metrics(self, hand_reading, now: float | None = None):
        if hand_reading is None:
            return None
        fingers = hand_reading.fingers
        palm_scale = max(float(hand_reading.palm.scale), 1e-6)
        thumb_tip = np.array(hand_reading.landmarks[4][:2], dtype=np.float32)
        index_tip = np.array(hand_reading.landmarks[8][:2], dtype=np.float32)
        raw_distance = float(np.linalg.norm(thumb_tip - index_tip)) / palm_scale
        if self._window_pair_smoothed_distance is None:
            smoothed_distance = raw_distance
        else:
            smoothed_distance = 0.28 * raw_distance + 0.72 * self._window_pair_smoothed_distance
        thumb_ready = (
            fingers["thumb"].state in {"fully_open", "partially_curled", "mostly_curled"}
            and float(fingers["thumb"].openness) >= 0.16
        )
        index_ready = (
            fingers["index"].state in {"fully_open", "partially_curled", "mostly_curled"}
            and float(fingers["index"].openness) >= 0.16
        )
        folded_rest = all(
            fingers[name].state in {"mostly_curled", "closed"}
            or float(getattr(fingers[name], "curl", 0.0) or 0.0) >= 0.40
            or float(getattr(fingers[name], "openness", 0.0) or 0.0) <= 0.56
            for name in ("middle", "ring", "pinky")
        )
        active = bool(thumb_ready and index_ready and folded_rest)
        if active:
            self._window_pair_smoothed_distance = smoothed_distance
            if now is not None:
                self._window_pair_last_seen_at = float(now)
            self._window_pair_overlay = {
                "thumb": (float(thumb_tip[0]), float(thumb_tip[1])),
                "index": (float(index_tip[0]), float(index_tip[1])),
                "raw_distance": raw_distance,
                "distance": smoothed_distance,
            }
            return self._window_pair_overlay
        self._window_pair_smoothed_distance = None
        self._window_pair_overlay = None
        self._window_pair_last_seen_at = 0.0
        return None

    def _window_pair_state(self, distance_ratio: float) -> str:
        value = float(distance_ratio)
        if value <= 0.40:
            return "pinched"
        if value >= 0.54:
            return "apart"
        if 0.30 <= value <= 0.66:
            return "mid"
        return "neutral"

    def _window_close_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        palm_scale = max(float(hand_reading.palm.scale), 1e-6)
        landmarks = hand_reading.landmarks
        thumb_index_ratio = float(np.linalg.norm((landmarks[4] - landmarks[8])[:2])) / palm_scale
        thumb_out = (fingers["thumb"].state == "fully_open" and fingers["thumb"].openness >= 0.70 and fingers["thumb"].palm_distance >= 0.72)
        folded_core = all(fingers[name].state in {"mostly_curled", "closed"} for name in ("index", "middle", "ring", "pinky"))
        # Reject thumbs-up: the thumb tip (landmark 4) must NOT be higher on
        # screen than the middle finger's MCP base joint (landmark 9). Screen
        # Y grows downward, so "higher" means smaller Y. The close-window
        # gesture is meant to be a horizontal / sideways thumb, not a vertical
        # thumbs-up signal the user often makes in unrelated contexts.
        thumb_tip_y = float(landmarks[4][1])
        middle_mcp_y = float(landmarks[9][1])
        thumb_not_above_middle_base = thumb_tip_y >= middle_mcp_y
        return thumb_out and folded_core and thumb_index_ratio >= 0.62 and thumb_not_above_middle_base

    def _handle_window_control_gestures(self, hand_reading, hand_handedness: str | None, now: float) -> bool:
        if hand_handedness != "Right" or hand_reading is None:
            self._reset_window_gesture_state(clear_cooldown=False)
            return False
        if now < self._window_gesture_cooldown_until:
            return False
        controller = self.voice_processor.desktop_controller
        if self._window_close_pose_active(hand_reading):
            if self._window_close_candidate_since <= 0.0:
                self._window_close_candidate_since = now
            if now - self._window_close_candidate_since >= 1.0:
                success = controller.close_active_window()
                self._chrome_control_text = controller.message
                self._spotify_control_text = controller.message
                if success:
                    self.command_detected.emit(controller.message)
                    self._window_gesture_cooldown_until = now + 2.0
                self._reset_window_gesture_state(clear_cooldown=False)
                return True
            return True
        self._window_close_candidate_since = 0.0
        metrics = self._window_pair_pose_metrics(hand_reading, now=now)
        if metrics is None:
            self._window_sequence_start_state = None
            self._window_sequence_start_candidate = None
            self._window_sequence_start_candidate_since = 0.0
            self._window_sequence_target_candidate = None
            self._window_sequence_target_candidate_since = 0.0
            return False
        state = self._window_pair_state(float(metrics["distance"]))
        if self._window_sequence_start_state is None:
            if state not in {"apart", "pinched"}:
                self._window_sequence_start_candidate = None
                self._window_sequence_start_candidate_since = 0.0
                return True
            if state != self._window_sequence_start_candidate:
                self._window_sequence_start_candidate = state
                self._window_sequence_start_candidate_since = now
                return True
            if now - self._window_sequence_start_candidate_since >= 1.3:
                self._window_sequence_start_state = state
                self._window_sequence_target_candidate = None
                self._window_sequence_target_candidate_since = 0.0
                label = "expanded" if state == "apart" else "pinched"
                self._chrome_control_text = f"window control start: {label}"
                self._spotify_control_text = self._chrome_control_text
            return True
        start_state = self._window_sequence_start_state
        target_state = None
        action = None
        if start_state == "apart":
            if state == "pinched":
                target_state = "pinched"
                action = "minimize"
            elif state == "mid":
                target_state = "mid"
                action = "restore"
        elif start_state == "pinched":
            if state == "apart":
                target_state = "apart"
                action = "maximize"
            elif state == "mid":
                target_state = "mid"
                action = "restore"
        if target_state is None:
            self._window_sequence_target_candidate = None
            self._window_sequence_target_candidate_since = 0.0
            return True
        if target_state != self._window_sequence_target_candidate:
            self._window_sequence_target_candidate = target_state
            self._window_sequence_target_candidate_since = now
            return True
        if now - self._window_sequence_target_candidate_since < 1.0:
            return True
        if action == "maximize":
            success = controller.maximize_active_window()
        elif action == "minimize":
            success = controller.minimize_active_window()
        else:
            success = controller.restore_active_window()
        self._chrome_control_text = controller.message
        self._spotify_control_text = controller.message
        if success:
            self.command_detected.emit(controller.message)
            self._window_gesture_cooldown_until = now + 2.0
        self._reset_window_gesture_state(clear_cooldown=False)
        return True

    def _normalize_result_right_primary(self, result):
        if not getattr(result, "found", False) or result.tracked_hand is None:
            return result
        primary_label = result.tracked_hand.handedness
        secondary = getattr(result, "secondary_tracked_hand", None)
        sec_label = secondary.handedness if secondary is not None else None
        if primary_label != "Right" and sec_label == "Right":
            return SimpleNamespace(
                found=True,
                frame_index=getattr(result, "frame_index", 0),
                tracked_hand=result.secondary_tracked_hand,
                hand_reading=getattr(result, "secondary_hand_reading", None),
                prediction=getattr(result, "secondary_prediction", None),
                annotated_frame=result.annotated_frame,
                secondary_tracked_hand=result.tracked_hand,
                secondary_hand_reading=result.hand_reading,
                secondary_prediction=result.prediction,
            )
        return result

    def _draw_window_control_overlay(self, frame) -> None:
        overlay = self._window_pair_overlay
        if not overlay or frame is None:
            return
        frame_h, frame_w = frame.shape[:2]
        thumb = overlay.get("thumb")
        index = overlay.get("index")
        if thumb is None or index is None:
            return
        p1 = (int(round(max(0.0, min(1.0, float(thumb[0]))) * max(frame_w - 1, 1))), int(round(max(0.0, min(1.0, float(thumb[1]))) * max(frame_h - 1, 1))))
        p2 = (int(round(max(0.0, min(1.0, float(index[0]))) * max(frame_w - 1, 1))), int(round(max(0.0, min(1.0, float(index[1]))) * max(frame_h - 1, 1))))
        cv2.line(frame, p1, p2, (255, 248, 212), 2, cv2.LINE_AA)
        cv2.circle(frame, p1, 6, (36, 220, 184), thickness=-1, lineType=cv2.LINE_AA)
        cv2.circle(frame, p2, 6, (36, 220, 184), thickness=-1, lineType=cv2.LINE_AA)
        mid_x = int(round((p1[0] + p2[0]) * 0.5))
        mid_y = int(round((p1[1] + p2[1]) * 0.5))
        ratio = float(overlay.get("distance", 0.0) or 0.0)
        cv2.putText(frame, f"{ratio:.2f}", (mid_x + 8, mid_y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 255, 255), 2, cv2.LINE_AA)
    def set_tutorial_context(self, enabled: bool, step_key: str | None = None) -> None:
        self._tutorial_mode_enabled = bool(enabled)
        if enabled:
            normalized_step = str(step_key or "").strip()
            self._tutorial_step_key = normalized_step or None
        else:
            self._tutorial_step_key = None
        if not self._tutorial_mode_enabled or self._tutorial_step_key != "gesture_wheel":
            self._reset_chrome_wheel()
            self._reset_spotify_wheel()
            self._reset_youtube_wheel()
        if not self._tutorial_mode_enabled:
            self.chrome_router.reset()
            self.spotify_router.reset()

    def _build_mouse_tracker(self) -> MouseGestureTracker:
        return MouseGestureTracker(
            control_box_center_x=self.config.mouse_control_box_center_x,
            control_box_center_y=self.config.mouse_control_box_center_y,
            control_box_area=self.config.mouse_control_box_area,
            control_box_aspect_power=self.config.mouse_control_box_aspect_power,
        )

    def _blank_mouse_update(self):
        return SimpleNamespace(
            mode_enabled=False,
            cursor_position=None,
            left_click=False,
            left_press=False,
            left_release=False,
            right_click=False,
            scroll_steps=0,
        )

    def apply_config(self, config) -> None:
        self.config = config
        self.volume_overlay.apply_theme(config)
        self.voice_status_overlay.apply_theme(config)
        self.spotify_wheel_overlay.apply_theme(config)
        self.chrome_wheel_overlay.apply_theme(config)
        self.youtube_wheel_overlay.apply_theme(config)
        self.drawing_wheel_overlay.apply_theme(config)
        self.utility_wheel_overlay.apply_theme(config)
        self.mouse_tracker = self._build_mouse_tracker()

    def _refresh_fullscreen_foreground(self, now: float) -> None:
        if (now - self._fullscreen_check_last) < self._FULLSCREEN_POLL_INTERVAL:
            return
        self._fullscreen_check_last = now
        try:
            active = bool(is_foreground_fullscreen())
        except Exception:
            active = False
        process = ""
        if active:
            try:
                info = get_foreground_window_info()
                if info is not None:
                    process = info.process_name or ""
            except Exception:
                process = ""
        self._fullscreen_foreground_active = active
        self._fullscreen_foreground_process = process

    def _engage_auto_low_fps(self) -> None:
        self._low_fps_auto_engaged = True
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
        self.engine = self._build_engine_for_fps_mode()
        if self._cap is not None:
            self._apply_low_fps_capture_tuning(self._cap)

    def _disengage_auto_low_fps(self) -> None:
        self._low_fps_auto_engaged = False
        self._low_fps_below_since = None
        self._low_fps_above_since = None
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
        self.engine = self._build_engine_for_fps_mode()
        if self._cap is not None and not self._low_fps_active:
            self._restore_normal_capture_tuning(self._cap)

    def _maybe_auto_toggle_low_fps(self, now: float) -> None:
        if getattr(self.config, "low_fps_mode", False):
            return
        # Only auto-engage when a fullscreen app (typically a game) has
        # foreground focus and is starving us of CPU. Without this gate,
        # transient stalls during normal desktop use would thrash the engine.
        fullscreen = self._fullscreen_foreground_active
        if not fullscreen:
            if self._low_fps_auto_engaged:
                self._disengage_auto_low_fps()
            else:
                self._low_fps_below_since = None
                self._low_fps_above_since = None
            return
        fps = self._fps
        if fps <= 0.0:
            return
        if fps < self._LOW_FPS_AUTO_THRESHOLD:
            self._low_fps_above_since = None
            if self._low_fps_below_since is None:
                self._low_fps_below_since = now
            elif not self._low_fps_auto_engaged and (now - self._low_fps_below_since) >= self._LOW_FPS_AUTO_ENTER_SECONDS:
                self._engage_auto_low_fps()
        else:
            self._low_fps_below_since = None
            if self._low_fps_auto_engaged:
                if self._low_fps_above_since is None:
                    self._low_fps_above_since = now
                elif (now - self._low_fps_above_since) >= self._LOW_FPS_AUTO_EXIT_SECONDS and fps >= self._LOW_FPS_AUTO_THRESHOLD:
                    self._disengage_auto_low_fps()

    def _maybe_offer_low_fps_suggestion(self, now: float) -> None:
        """Track sustained low FPS and show the suggestion overlay when warranted.

        Independent of the fullscreen-gated auto-engage: the suggestion is a
        user-visible offer, not a silent switch, so it should fire on any
        prolonged FPS drop regardless of whether a game is foregrounded.
        Suppressed when low-fps is already on, when we're inside the post-
        dismiss cooldown, or when the toast is already visible.
        """
        if getattr(self.config, "low_fps_mode", False) or self._low_fps_auto_engaged:
            # Already on (user or auto); no reason to suggest.
            self._low_fps_suggest_below_since = None
            return
        if self._low_fps_suggest_visible:
            return
        if now < self._low_fps_suggest_cooldown_until:
            self._low_fps_suggest_below_since = None
            return
        fps = self._fps
        if fps <= 0.0:
            return
        if fps < self._LOW_FPS_SUGGEST_THRESHOLD:
            if self._low_fps_suggest_below_since is None:
                self._low_fps_suggest_below_since = now
            elif (now - self._low_fps_suggest_below_since) >= self._LOW_FPS_SUGGEST_ENTER_SECONDS:
                self._show_low_fps_suggestion()
        else:
            self._low_fps_suggest_below_since = None

    def _show_low_fps_suggestion(self) -> None:
        try:
            self.low_fps_suggestion_overlay.show_suggestion()
            self._low_fps_suggest_visible = True
        except Exception:
            pass

    def _handle_low_fps_suggestion_activate(self) -> None:
        # User clicked "Low FPS Mode" on the toast — flip the persistent
        # setting and apply it live. Mirror the path the Settings button uses.
        try:
            self.set_low_fps_mode(True)
        except Exception:
            pass
        try:
            save_config(self.config)
        except Exception:
            pass
        self._low_fps_suggest_below_since = None
        self._low_fps_suggest_cooldown_until = time.time() + self._LOW_FPS_SUGGEST_COOLDOWN_SECONDS

    def _handle_low_fps_suggestion_dismissed(self) -> None:
        self._low_fps_suggest_visible = False
        self._low_fps_suggest_below_since = None
        self._low_fps_suggest_cooldown_until = time.time() + self._LOW_FPS_SUGGEST_COOLDOWN_SECONDS

    def _dismiss_low_fps_suggestion_via_gesture(self) -> None:
        if self._low_fps_suggest_visible:
            try:
                self.low_fps_suggestion_overlay.dismiss()
            except Exception:
                pass

    def _build_engine_for_fps_mode(self) -> GestureRecognitionEngine:
        self._low_fps_active = bool(getattr(self.config, "low_fps_mode", False)) or self._low_fps_auto_engaged
        if self._low_fps_active:
            detector = HandDetector(
                min_detection_confidence=0.34,
                min_tracking_confidence=0.22,
                model_complexity=0,
                miss_tolerance_seconds=0.24,
                max_process_width=self._LOW_FPS_PROCESS_WIDTH,
                smoother=AdaptiveLandmarkSmoother(alpha=0.66, min_alpha=0.24, max_alpha=0.88),
                secondary_smoother=AdaptiveLandmarkSmoother(alpha=0.66, min_alpha=0.24, max_alpha=0.88),
            )
            stable_frames = 1
        else:
            detector = HandDetector(max_process_width=self._NORMAL_PROCESS_WIDTH)
            stable_frames = max(2, self.config.stable_frames_required // 2)
        return GestureRecognitionEngine(
            detector=detector,
            stable_frames_required=stable_frames,
            low_fps_mode=self._low_fps_active,
        )

    def set_low_fps_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.config.low_fps_mode = enabled
        if not enabled:
            self._low_fps_auto_engaged = False
            self._low_fps_below_since = None
            self._low_fps_above_since = None
        self._low_fps_last_process = 0.0
        if self._running:
            if self.engine is not None:
                try:
                    self.engine.close()
                except Exception:
                    pass
            self.engine = self._build_engine_for_fps_mode()
            self._fps = 0.0
            if self._cap is not None:
                if self._low_fps_active:
                    self._apply_low_fps_capture_tuning(self._cap)
                else:
                    self._restore_normal_capture_tuning(self._cap)

    def set_force_ten_fps_test_mode(self, enabled: bool) -> None:
        self.config.force_ten_fps_test_mode = bool(enabled)
        self._low_fps_last_process = 0.0

    def _should_skip_forced_fps_tick(self, now: float) -> bool:
        if not bool(getattr(self.config, "force_ten_fps_test_mode", False)):
            self._low_fps_last_process = 0.0
            return False
        target_fps = max(1.0, float(self._FORCED_TEST_FPS_TARGET))
        min_interval = 1.0 / target_fps
        if self._low_fps_last_process <= 0.0:
            self._low_fps_last_process = now
            return False
        if (now - self._low_fps_last_process) < min_interval:
            return True
        self._low_fps_last_process = now
        return False

    def start(self) -> None:
        if self._running:
            return
        self._shutdown_runtime(emit_signal=False)
        self.engine = self._build_engine_for_fps_mode()
        self._volume_message = self.volume_controller.message
        self._volume_level = self.volume_controller.refresh_cache().level_scalar
        self._volume_mode_active = False
        self._volume_status_text = "idle"
        self._volume_muted = self._read_system_mute()
        self._volume_overlay_visible = False
        self._mute_block_until = 0.0
        self.volume_tracker.reset(self._volume_level, self._volume_muted)
        self.volume_overlay.hide_overlay()
        self.voice_status_overlay.hide_overlay()
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._reset_chrome_wheel(clear_cooldown=True)
        self._reset_spotify_wheel(clear_cooldown=True)
        self._reset_youtube_wheel(clear_cooldown=True)
        self.chrome_router.reset()
        self._chrome_mode_enabled = False
        self._chrome_control_text = self.chrome_controller.message
        self._last_chrome_action = "-"
        self.spotify_router.reset()
        self._spotify_control_text = self.spotify_controller.message
        self._spotify_info_text = "-"
        self._last_spotify_action = "-"
        self.youtube_router.reset()
        self._youtube_control_text = "youtube idle"
        self._youtube_mode_info = "off"
        self._last_chrome_action_counter = 0
        self._last_spotify_action_counter = 0
        self._last_youtube_action_counter = 0
        self._reset_voice_state()
        self._reset_drawing_runtime()
        self._reset_drawing_wheel(clear_cooldown=True)
        self._reset_utility_wheel(clear_cooldown=True)
        self._camera_draw_canvas = None
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        self._drawing_request_action = ""
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_request_token = 0
        self._utility_request_action = ""
        self._utility_request_token = 0
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._reset_window_gesture_state(clear_cooldown=True)

        camera_info, cap = self._open_camera()
        if cap is None or camera_info is None:
            self._emit_status("no camera found")
            self.error_occurred.emit("No available camera was found.")
            self.running_state_changed.emit(False)
            return

        self._camera_info = camera_info
        self._cap = cap
        self._running = True
        self._last_time = time.time()
        self._fps = 0.0
        self._low_fps_last_process = 0.0
        self._fullscreen_foreground_active = False
        self._fullscreen_foreground_process = ""
        self._fullscreen_check_last = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0
        self.camera_selected.emit(camera_info.display_name)
        self._emit_status("Touchless active")
        self.command_detected.emit("Gesture and voice control active")
        self.running_state_changed.emit(True)
        self._timer.start()

        def _warm_spotify() -> None:
            try:
                self.spotify_controller.warm_up()
            except Exception:
                pass

        import threading
        threading.Thread(target=_warm_spotify, daemon=True).start()

    def stop(self) -> None:
        if not self._running and self._cap is None and self.engine is None:
            return
        self._shutdown_runtime(emit_signal=True)

    def _shutdown_runtime(self, *, emit_signal: bool) -> None:
        self._timer.stop()
        if self._cap is not None:
            # Don't release the phone-camera-QR capture — it's owned by the
            # MainWindow/PhoneCameraServer and must survive engine restarts
            # (e.g. toggling Low FPS Mode re-opens the camera via start()).
            if self._cap is not self._phone_camera_capture:
                self._cap.release()
            self._cap = None
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        self._camera_info = None
        self._running = False
        self._fps = 0.0
        self._low_fps_last_process = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0
        self._volume_mode_active = False
        self._volume_overlay_visible = False
        self.volume_overlay.hide_overlay()
        self.voice_status_overlay.hide_overlay()
        try:
            self.low_fps_suggestion_overlay.hide()
        except Exception:
            pass
        self._low_fps_suggest_visible = False
        self._low_fps_suggest_below_since = None
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._reset_chrome_wheel(clear_cooldown=True)
        self._reset_spotify_wheel(clear_cooldown=True)
        self._reset_youtube_wheel(clear_cooldown=True)
        self.chrome_router.reset()
        self.spotify_router.reset()
        self.volume_tracker.reset(self._volume_level, self._volume_muted)
        self._reset_voice_state()
        self._reset_drawing_runtime()
        self._reset_drawing_wheel(clear_cooldown=True)
        self._reset_utility_wheel(clear_cooldown=True)
        self._camera_draw_canvas = None
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        self._drawing_request_action = ""
        self._drawing_swipe_cooldown_until = 0.0
        self._utility_request_action = ""
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._reset_window_gesture_state(clear_cooldown=True)
        self._emit_status("idle")
        if emit_signal:
            self.running_state_changed.emit(False)

    def set_phone_camera_capture(self, capture) -> None:
        """Hand the engine a running PhoneCameraCapture from the QR-dialog flow.

        When set, _open_camera() will use this capture in place of any
        local-device or URL-based source. Callers set None to clear it
        (e.g. when the user disconnects the phone).
        """
        self._phone_camera_capture = capture

    def _open_camera(self):
        # QR-dialog path wins when present: the server is already running
        # on a worker thread and frames are flowing; we just wrap its
        # capture in the (info, cap) shape the engine expects. A local
        # camera_index_override still beats this (used by the in-app
        # camera test flow).
        phone_qr_capture = getattr(self, "_phone_camera_capture", None)
        phone_url = str(getattr(self.config, "phone_camera_url", "") or "").strip()
        use_phone_url = bool(getattr(self.config, "phone_camera_enabled", False)) and phone_url
        if self.camera_index_override is not None:
            result = open_camera_by_index(self.camera_index_override, max_index=self.config.camera_scan_limit)
        elif phone_qr_capture is not None and phone_qr_capture.isOpened():
            info = SimpleNamespace(
                index=-2,
                backend=0,
                backend_name="PhoneQR",
                display_name="Phone Camera (QR)",
            )
            result = (info, phone_qr_capture)
        elif use_phone_url:
            result = open_phone_camera_url(phone_url)
            if result[1] is None:
                # Phone camera unreachable at startup — fall back to the last
                # preferred local camera so the app can still run. The UI will
                # reflect this via the live-status path (status_changed signal).
                result = open_preferred_or_first_available(self.config.preferred_camera_index, max_index=self.config.camera_scan_limit)
        else:
            result = open_preferred_or_first_available(self.config.preferred_camera_index, max_index=self.config.camera_scan_limit)
        self._apply_low_fps_capture_tuning(result)
        return result

    def _draw_low_fps_badge(self, frame) -> None:
        try:
            height, width = frame.shape[:2]
        except Exception:
            return
        text = "Low FPS Mode"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.6
        thickness = 2
        (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
        pad_x, pad_y = 12, 8
        x1 = 14
        y1 = 14
        x2 = x1 + text_w + pad_x * 2
        y2 = y1 + text_h + pad_y * 2
        overlay = frame.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 215, 255), thickness=-1)
        cv2.addWeighted(overlay, 0.82, frame, 0.18, 0, frame)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 120, 170), thickness=2)
        cv2.putText(
            frame,
            text,
            (x1 + pad_x, y2 - pad_y - 2),
            font,
            scale,
            (20, 20, 20),
            thickness,
            cv2.LINE_AA,
        )

    def _apply_low_fps_capture_tuning(self, open_result) -> None:
        if not self._low_fps_active:
            return
        cap = None
        if isinstance(open_result, tuple) and len(open_result) >= 2:
            cap = open_result[1]
        elif open_result is not None and hasattr(open_result, "set"):
            cap = open_result
        if cap is None:
            return
        try:
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            if hasattr(cv2, "CAP_PROP_FOURCC"):
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        try:
            if hasattr(cv2, "CAP_PROP_FPS"):
                cap.set(cv2.CAP_PROP_FPS, 30)
        except Exception:
            pass
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        except Exception:
            pass

    def _restore_normal_capture_tuning(self, open_result) -> None:
        cap = None
        if isinstance(open_result, tuple) and len(open_result) >= 2:
            cap = open_result[1]
        elif open_result is not None and hasattr(open_result, "set"):
            cap = open_result
        if cap is None:
            return
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            if hasattr(cv2, "CAP_PROP_FPS"):
                cap.set(cv2.CAP_PROP_FPS, 30)
        except Exception:
            pass

    def _prepare_runtime_frame(self, frame):
        if frame is None:
            return frame
        try:
            height, width = frame.shape[:2]
        except Exception:
            return frame
        if not self._low_fps_active or width <= 640 or height <= 0:
            return frame
        scaled_height = max(1, int(round(height * (640.0 / float(width)))))
        try:
            return cv2.resize(frame, (640, scaled_height), interpolation=cv2.INTER_AREA)
        except Exception:
            return frame

    def _tick(self) -> None:
        if not self._running or self._cap is None or self.engine is None:
            return
        tick_now = time.time()
        if self._should_skip_forced_fps_tick(tick_now):
            return
        ok, frame = self._cap.read()
        if not ok:
            return
        # Touchless normally mirrors the camera feed so the user sees the
        # "selfie" view a webcam gives by convention. If the source already
        # outputs a mirrored image (some phone-camera apps like Iriun do
        # this when their own mirror toggle is on), a second flip here
        # produces an un-mirrored view, which is what the user was hitting.
        # The config toggle lets them skip our flip.
        if not bool(getattr(self.config, "camera_source_is_mirrored", False)):
            frame = cv2.flip(frame, 1)
        frame = self._prepare_runtime_frame(frame)
        result = self.engine.process_frame(frame)
        self._drawing_secondary_hand_reading = getattr(result, "secondary_hand_reading", None)
        now = time.time()
        dt = max(now - self._last_time, 1e-6)
        self._fps = 0.86 * self._fps + 0.14 * (1.0 / dt) if self._fps else (1.0 / dt)
        self._last_time = now
        self._refresh_fullscreen_foreground(now)
        self._maybe_auto_toggle_low_fps(now)
        self._maybe_offer_low_fps_suggestion(now)
        self._drain_voice_results()
        if not self._dictation_active:
            try:
                self.text_input_controller.remember_active_window()
            except Exception:
                pass
        monotonic_now = time.monotonic()
        result = self._normalize_result_right_primary(result)
        hand_handedness = result.tracked_hand.handedness if result.found and result.tracked_hand is not None else None
        # Only treat a hand as "left" when BOTH hands are visible —
        # one labeled Left and one labeled Right. MediaPipe will
        # confidently label a single visible right hand as "Left"
        # in some poses (the model only sees the hand silhouette,
        # not the user's body), which used to trigger voice
        # listening unexpectedly while doing right-hand-only
        # gestures. Requiring two distinct hands eliminates the
        # ambiguity at the cost of users who actually want to use
        # the voice trigger one-handed (rare; they can keep their
        # other hand in the frame).
        left_prediction = None
        secondary = getattr(result, "secondary_tracked_hand", None)
        sec_handedness = secondary.handedness if secondary is not None else None
        both_hands_visible = (
            result.found
            and hand_handedness in {"Left", "Right"}
            and sec_handedness in {"Left", "Right"}
            and hand_handedness != sec_handedness
        )
        if both_hands_visible:
            if hand_handedness == "Left":
                left_prediction = result.prediction
            else:
                left_prediction = getattr(result, "secondary_prediction", None)
        self._left_hand_prediction = left_prediction
        if self._gestures_enabled:
            if self._drawing_mode_enabled:
                self._volume_mode_active = False
                self._volume_status_text = "paused"
                self._volume_message = "drawing mode active"
                self._volume_overlay_visible = False
                self._update_volume_overlay()
            else:
                self._handle_volume_control(result, monotonic_now, hand_handedness=hand_handedness)
            self._handle_app_controls(result.prediction, result.hand_reading, hand_handedness, monotonic_now)
        else:
            self._window_pair_pose_metrics(result.hand_reading if hand_handedness == "Right" else None, now=monotonic_now)
        self._update_chrome_wheel_overlay(monotonic_now)
        self._update_spotify_wheel_overlay(monotonic_now)
        self._update_youtube_wheel_overlay(monotonic_now)
        self._update_drawing_wheel_overlay(monotonic_now)
        self._update_utility_wheel_overlay(monotonic_now)
        self.voice_status_overlay.tick(monotonic_now)
        self._update_runtime_status()
        display_frame = draw_hand_overlay(result.annotated_frame, result)
        self._draw_window_control_overlay(display_frame)
        self._update_camera_drawing_canvas(display_frame.shape)
        self._blend_camera_drawing_overlay(display_frame)
        draw_mouse_control_box_overlay(
            display_frame,
            debug_state=self.mouse_tracker.debug_state,
            mode_enabled=self._mouse_mode_enabled,
        )
        draw_mouse_monitor_overlay(
            display_frame,
            mouse_controller=self.mouse_controller,
            debug_state=self.mouse_tracker.debug_state,
            mode_enabled=self._mouse_mode_enabled,
        )
        if self._low_fps_active:
            self._draw_low_fps_badge(display_frame)
        payload = self._build_debug_payload(result, monotonic_now)
        try:
            self.debug_frame_ready.emit(display_frame, payload)
        except Exception:
            pass

    def _handle_volume_control(self, result, now: float, *, hand_handedness: str | None) -> None:
        if not self.volume_controller.available:
            self._volume_message = self.volume_controller.message
            self._volume_mode_active = False
            self._volume_status_text = "unavailable"
            self._volume_overlay_visible = False
            self._update_volume_overlay()
            return

        current_level = self.volume_controller.get_level()
        current_muted = self._read_system_mute()
        if self.mouse_tracker.mode_enabled:
            self._volume_level = current_level
            self._volume_muted = current_muted
            self._volume_message = "mouse mode active"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._volume_dual_active = False
            self._volume_init_palm_x = None
            self._update_volume_overlay()
            return
        if self._drawing_mode_enabled:
            self._volume_level = current_level
            self._volume_muted = current_muted
            self._volume_message = "drawing mode active"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._volume_dual_active = False
            self._volume_init_palm_x = None
            self._update_volume_overlay()
            return
        if hand_handedness == "Right" and result.prediction.dynamic_label in {"swipe_left", "swipe_right"}:
            self._mute_block_until = max(self._mute_block_until, now + 0.5)

        features = None
        candidate_scores = {}
        landmarks = None
        stable_gesture = "neutral"
        if (
            hand_handedness == "Right"
            and result.found
            and result.tracked_hand is not None
            and result.hand_reading is not None
            and self.engine is not None
        ):
            landmarks = result.tracked_hand.landmarks
            features = self._volume_features_from_hand_reading(result.hand_reading)
            candidate_scores = self.engine.last_static_scores
            stable_gesture = result.prediction.stable_label

        tracker_level = current_level
        if self._volume_dual_active and self._volume_bar_selected == "app" and self._volume_overlay_visible:
            tracker_level = self._volume_app_level if self._volume_app_level is not None else current_level

        palm_roll = None
        if (
            result.found
            and result.tracked_hand is not None
            and result.hand_reading is not None
        ):
            try:
                palm_roll = float(result.hand_reading.palm.roll_deg)
            except Exception:
                palm_roll = None
        update = self.volume_tracker.update(
            features=features,
            landmarks=landmarks,
            candidate_scores=candidate_scores,
            stable_gesture=stable_gesture,
            current_level=tracker_level,
            current_muted=current_muted,
            now=now,
            allow_mute_toggle=now >= self._mute_block_until,
            palm_roll_deg=palm_roll,
        )

        entering_overlay = self._volume_overlay_visible is False and update.overlay_visible
        if entering_overlay:
            refreshed = self.volume_controller.refresh_cache()
            if refreshed.level_scalar is not None:
                current_level = refreshed.level_scalar
            refreshed_muted = self.volume_controller.get_mute()
            if refreshed_muted is not None:
                current_muted = bool(refreshed_muted)
            palm_center = getattr(getattr(result.hand_reading, "palm", None), "center", None) if result.hand_reading is not None else None
            self._volume_init_palm_x = float(palm_center[0]) if palm_center is not None else None
            self._volume_bar_selected = "sys"
            youtube_active = self._youtube_mode_info in {"forced", "auto"}
            if youtube_active:
                app_name, app_level = self.volume_controller.get_app_audio_info(["chrome"])
                if app_name == "chrome":
                    self._volume_dual_active = True
                    self._volume_app_process = "youtube"
                    self._volume_app_label = "YouTube"
                    self._volume_app_level = app_level if app_level is not None else (current_level if current_level is not None else 0.5)
                    self._volume_bar_selected = "app"
                    self.volume_tracker.rebase(self._volume_app_level)
                else:
                    self._volume_dual_active = False
                    self._volume_app_process = ""
                    self._volume_app_label = ""
                    self._volume_app_level = None
            else:
                app_name, app_level = self.volume_controller.get_active_app_audio_info(
                    ["spotify", "chrome"]
                )
                self._volume_dual_active = app_name is not None
                self._volume_app_process = app_name or ""
                self._volume_app_label = app_name.capitalize() if app_name else ""
                if app_name == "spotify":
                    spotify_vol = self.spotify_controller.get_volume()
                    self._volume_app_level = spotify_vol / 100.0 if spotify_vol is not None else (app_level or 0.5)
                else:
                    self._volume_app_level = app_level
            self._volume_app_check_until = now + 3.0

        bar_switched_this_frame = False
        if update.overlay_visible and self._volume_dual_active:
            palm_center = getattr(getattr(result.hand_reading, "palm", None), "center", None) if result.hand_reading is not None else None
            if palm_center is not None and self._volume_init_palm_x is not None:
                dx = float(palm_center[0]) - self._volume_init_palm_x
                previous_bar = self._volume_bar_selected
                if dx < -0.06:
                    self._volume_bar_selected = "app"
                elif dx > 0.06:
                    self._volume_bar_selected = "sys"
                if self._volume_bar_selected != previous_bar:
                    bar_switched_this_frame = True
                    if self._volume_bar_selected == "app":
                        rebase_level = self._volume_app_level if self._volume_app_level is not None else current_level
                    else:
                        rebase_level = current_level
                    self.volume_tracker.rebase(rebase_level)

        controller_error_message: str | None = None
        controller_error_status: str | None = None
        if update.trigger_mute_toggle:
            toggled = self.volume_controller.toggle_mute()
            if toggled is not None:
                current_muted = toggled
                self.command_detected.emit("Volume mute toggled")
                self._record_action("volume_mute_on" if toggled else "volume_mute_off", "muted" if toggled else "unmuted")
            else:
                controller_error_message = self.volume_controller.message or "mute failed"
                controller_error_status = "error"

        if update.active and update.level is not None and not bar_switched_this_frame:
            if self._volume_dual_active and self._volume_bar_selected == "app":
                new_app = max(0.0, min(1.0, float(update.level)))
                if self._volume_app_process == "spotify":
                    vol_pct = int(round(new_app * 100))
                    self._queue_spotify_volume(vol_pct)
                    self._volume_app_level = new_app
                else:
                    if self.volume_controller.set_app_audio_level(["chrome"], new_app):
                        self._volume_app_level = new_app
                    else:
                        controller_error_message = "app volume adjust failed"
                        controller_error_status = "error"
            else:
                target_level = max(0.0, min(1.0, float(update.level)))
                if self.volume_controller.set_level(target_level):
                    current_level = target_level
                    read_back_level = self.volume_controller.get_level()
                    if read_back_level is not None:
                        current_level = read_back_level
                else:
                    controller_error_message = self.volume_controller.message or "set_level failed"
                    controller_error_status = "error"

        if update.overlay_visible and self._volume_dual_active and now >= self._volume_app_check_until and not (update.active and self._volume_bar_selected == "app"):
            self._volume_app_check_until = now + 3.0
            if self._volume_app_process == "spotify":
                def _refresh_spotify_vol():
                    vol = self.spotify_controller.get_volume()
                    if vol is not None:
                        self._volume_app_level = vol / 100.0
                threading.Thread(target=_refresh_spotify_vol, daemon=True).start()
            else:
                _, fresh_app = self.volume_controller.get_app_audio_info(["chrome"])
                if fresh_app is not None:
                    self._volume_app_level = fresh_app

        if not update.overlay_visible:
            self._volume_dual_active = False
            self._volume_init_palm_x = None

        self._volume_mode_active = update.active
        self._volume_level = current_level if current_level is not None else update.level
        self._volume_muted = current_muted
        self._volume_message = controller_error_message or update.message
        self._volume_status_text = controller_error_status or update.status
        overlay_was_visible = self._volume_overlay_visible
        if entering_overlay:
            self._volume_session_prev_level = current_level
            self._volume_session_prev_app_level = self._volume_app_level
        if overlay_was_visible and not update.overlay_visible:
            self._record_volume_session_end(current_level)
        self._volume_overlay_visible = update.overlay_visible
        self._update_volume_overlay()

    def _record_volume_session_end(self, final_level: float | None) -> None:
        prev = self._volume_session_prev_level
        self._volume_session_prev_level = None
        self._volume_session_prev_app_level = None
        if prev is None or final_level is None:
            return
        delta = float(final_level) - float(prev)
        if abs(delta) < 0.02:
            return
        direction = "up" if delta > 0 else "down"
        pct = int(round(float(final_level) * 100))
        self._record_action(f"volume_{direction}", f"volume {direction} to {pct}%")

    def _handle_app_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        if self._tutorial_mode_enabled:
            self._handle_tutorial_controls(prediction, hand_reading, hand_handedness, now)
            return

        # When a save-location prompt is awaiting input, give the left-hand voice handler
        # a chance to run before any mode-specific branch returns early. This keeps the
        # left-fist cancel gesture available even while drawing / mouse / volume mode is
        # still active underneath the prompt.
        if self._save_prompt_active and self._left_hand_prediction is not None:
            self._handle_left_hand_voice(self._left_hand_prediction, now)
            if self._save_prompt_active:
                return

        if self._utility_capture_selection_active:
            self._update_utility_capture_selection(hand_reading, hand_handedness)
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._utility_recording_active and hand_handedness == 'Right' and hand_reading is not None and self._utility_wheel_pose_active(hand_reading):
            if self._utility_recording_stop_candidate_since <= 0.0:
                self._utility_recording_stop_candidate_since = now
            elif now - self._utility_recording_stop_candidate_since >= 0.6:
                self._queue_utility_request('stop_recording')
                self.command_detected.emit('Stopping screen recording')
                self._utility_recording_stop_candidate_since = 0.0
                self._utility_wheel_cooldown_until = now + 1.2
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            return
        self._utility_recording_stop_candidate_since = 0.0

        utility_wheel_consuming = self._update_utility_wheel(hand_reading, hand_handedness, now)
        if utility_wheel_consuming:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._dictation_active:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if self._left_hand_prediction is not None:
                self._handle_left_hand_voice(self._left_hand_prediction, now)
            else:
                self._reset_voice_candidate(now)
            return

        if self._volume_overlay_visible or self._volume_mode_active:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if self._left_hand_prediction is not None:
                self._handle_left_hand_voice(self._left_hand_prediction, now)
            else:
                self._reset_voice_candidate(now)
            return

        drawing_toggle_consuming = self._handle_drawing_toggle(prediction, hand_handedness, now)
        if drawing_toggle_consuming:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if not self._drawing_mode_enabled:
                self._reset_drawing_wheel(clear_cooldown=False)
            self._chrome_control_text = self._drawing_control_text
            self._spotify_control_text = self._drawing_control_text
            return

        if self._drawing_mode_enabled:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._reset_window_gesture_state(clear_cooldown=False)
            drawing_wheel_consuming = self._update_drawing_wheel(prediction, hand_reading, now, active=hand_handedness == "Right")
            if drawing_wheel_consuming:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
                self._chrome_control_text = self._drawing_control_text
                self._spotify_control_text = self._drawing_control_text
                return
            self._update_drawing_controls(prediction, hand_reading, hand_handedness, now)
            if hand_handedness == "Right" and hand_reading is not None and self._drawing_tool == "hover" and now >= self._drawing_swipe_cooldown_until:
                dynamic_label = str(getattr(prediction, "dynamic_label", "neutral") or "neutral")
                if dynamic_label in {"swipe_left", "swipe_right"}:
                    if self._perform_drawing_swipe_action(dynamic_label):
                        self._drawing_swipe_cooldown_until = now + 1.2
                    self._chrome_control_text = self._drawing_control_text
                    self._spotify_control_text = self._drawing_control_text
                    return
            self._chrome_control_text = self._drawing_control_text
            self._spotify_control_text = self._drawing_control_text
            return

        if self._handle_window_control_gestures(hand_reading, hand_handedness, now):
            return

        mouse_consuming = self._handle_mouse_control(
            prediction=prediction,
            hand_reading=hand_reading if hand_handedness == "Right" else None,
            hand_handedness=hand_handedness,
            now=now,
        )
        if mouse_consuming:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._chrome_control_text = "mouse mode active"
            self._spotify_control_text = "mouse mode active"
            return

        if self._left_hand_prediction is not None:
            self._handle_left_hand_voice(self._left_hand_prediction, now)
            if hand_handedness != "Right":
                self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
                self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
                self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
                return
        else:
            self._reset_voice_candidate(now)
        app_static_label = self._derive_app_static_label(prediction, hand_reading)
        right_hand_active = hand_handedness == "Right"

        youtube_wheel_consuming = self._update_youtube_wheel(
            prediction,
            hand_reading,
            now,
            active=right_hand_active,
        )
        if youtube_wheel_consuming:
            return

        chrome_wheel_consuming = self._update_chrome_wheel(
            prediction,
            hand_reading,
            now,
            active=right_hand_active,
        )
        if chrome_wheel_consuming:
            return

        spotify_wheel_consuming = self._update_spotify_wheel(
            prediction,
            hand_reading,
            now,
            active=right_hand_active,
        )
        if spotify_wheel_consuming:
            return

        youtube_snapshot = self.youtube_router.update(
            stable_label=app_static_label,
            dynamic_label=prediction.dynamic_label,
            controller=self.youtube_controller,
            now=now,
        )
        self._youtube_control_text = youtube_snapshot.control_text
        self._youtube_mode_info = youtube_snapshot.info_text
        youtube_now_active = youtube_snapshot.info_text in {"forced", "auto"}
        if youtube_now_active != self._youtube_mode_prev_active:
            self._youtube_mode_prev_active = youtube_now_active
            label_text = "YouTube mode: ON" if youtube_now_active else "YouTube mode: OFF"
            try:
                self.voice_status_overlay.show_info_hint(label_text, duration=4.0)
            except Exception:
                pass
            try:
                self.command_detected.emit(label_text)
            except Exception:
                pass
            try:
                self._record_action(
                    "youtube_mode_on" if youtube_now_active else "youtube_mode_off",
                    label_text,
                )
            except Exception:
                pass
        if youtube_snapshot.action_counter != self._last_youtube_action_counter:
            self._last_youtube_action_counter = youtube_snapshot.action_counter
            if youtube_snapshot.last_action != "-":
                self.command_detected.emit(youtube_snapshot.control_text)
                self._record_action(youtube_snapshot.last_action, youtube_snapshot.control_text)

        if youtube_snapshot.consume_other_routes:
            return

        chrome_snapshot = self.chrome_router.update(
            stable_label=app_static_label,
            dynamic_label=prediction.dynamic_label,
            controller=self.chrome_controller,
            now=now,
        )
        self._chrome_mode_enabled = chrome_snapshot.mode_enabled
        self._chrome_control_text = chrome_snapshot.control_text
        chrome_now_active = bool(chrome_snapshot.mode_enabled)
        if chrome_now_active != self._chrome_mode_prev_active:
            self._chrome_mode_prev_active = chrome_now_active
            try:
                self.voice_status_overlay.show_info_hint(
                    "Chrome mode: ON" if chrome_now_active else "Chrome mode: OFF",
                    duration=3.0,
                )
            except Exception:
                pass
        if chrome_snapshot.action_counter != self._last_chrome_action_counter:
            self._last_chrome_action_counter = chrome_snapshot.action_counter
            if chrome_snapshot.last_action != "-":
                self.command_detected.emit(chrome_snapshot.control_text)
                self._record_action(chrome_snapshot.last_action, chrome_snapshot.control_text)

        if chrome_snapshot.consume_other_routes or app_static_label in {"three", "three_together"}:
            return

        if youtube_snapshot.mode_active:
            return

        snapshot = self.spotify_router.update(
            stable_label=prediction.stable_label,
            dynamic_label=prediction.dynamic_label,
            controller=self.spotify_controller,
            now=now,
        )
        self._spotify_control_text = snapshot.control_text
        self._spotify_info_text = snapshot.info_text
        if snapshot.action_counter != self._last_spotify_action_counter:
            self._last_spotify_action_counter = snapshot.action_counter
            if snapshot.last_action != "-":
                self.command_detected.emit(snapshot.control_text)
                self._record_action(snapshot.last_action, snapshot.control_text)

    def _handle_tutorial_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        step_key = self._tutorial_step_key or ""

        utility_wheel_consuming = self._update_utility_wheel(hand_reading, hand_handedness, now)
        if utility_wheel_consuming:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._dictation_active:
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if step_key == "voice_command" and self._left_hand_prediction is not None:
                self._handle_left_hand_voice(self._left_hand_prediction, now)
            else:
                self._reset_voice_candidate(now)
            return

        if step_key == "mouse_mode":
            mouse_consuming = self._handle_mouse_control(
                prediction=prediction,
                hand_reading=hand_reading if hand_handedness == "Right" else None,
                hand_handedness=hand_handedness,
                now=now,
            )
            self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if mouse_consuming:
                self._chrome_control_text = "mouse mode active"
                self._spotify_control_text = "mouse mode active"
            return

        self._reset_voice_candidate(now)
        self._update_youtube_wheel(prediction=None, hand_reading=None, now=now, active=False)
        self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)

        if self._mouse_mode_enabled:
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._chrome_control_text = "mouse mode active"
            self._spotify_control_text = "mouse mode active"
            return

        if step_key == "voice_command":
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if self._left_hand_prediction is not None:
                self._handle_left_hand_voice(self._left_hand_prediction, now)
            return

        if hand_handedness != "Right":
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self.chrome_router.reset()
            self.spotify_router.reset()
            return

        if step_key == "gesture_wheel":
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._update_spotify_wheel(
                prediction,
                hand_reading,
                now,
                active=hand_reading is not None,
            )
            return

        self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
        self.chrome_router.reset()

        if step_key == "swipes":
            self.spotify_router.reset()
            self._chrome_control_text = "tutorial swipe practice"
            self._spotify_control_text = "tutorial swipe practice"
            return

        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        if step_key == "spotify_open":
            routed_label = "two" if stable_label == "two" else "neutral"
        elif step_key == "play_pause":
            routed_label = "fist" if stable_label == "fist" else "neutral"
        else:
            self.spotify_router.reset()
            return

        snapshot = self.spotify_router.update(
            stable_label=routed_label,
            dynamic_label="neutral",
            controller=self.spotify_controller,
            now=now,
        )
        self._spotify_control_text = snapshot.control_text
        self._spotify_info_text = snapshot.info_text
        if snapshot.action_counter != self._last_spotify_action_counter:
            self._last_spotify_action_counter = snapshot.action_counter
            if snapshot.last_action != "-":
                self._last_spotify_action = snapshot.last_action
                self.command_detected.emit(snapshot.control_text)
                self._record_action(snapshot.last_action, snapshot.control_text)

    def _handle_mouse_control(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> bool:
        if not self.mouse_controller.available:
            self.mouse_tracker.reset()
            self._last_mouse_update = self._blank_mouse_update()
            self._mouse_mode_enabled = False
            self._mouse_status_text = "unavailable"
            self._mouse_control_text = self.mouse_controller.message
            return False

        self.mouse_tracker.set_desktop_bounds(self.mouse_controller.virtual_bounds())
        update = self.mouse_tracker.update(
            hand_reading=hand_reading,
            prediction=prediction,
            hand_handedness=hand_handedness,
            cursor_seed=self.mouse_controller.current_position_normalized(),
            now=now,
        )
        self._last_mouse_update = update

        tutorial_demo_only = self._tutorial_mode_enabled and self._tutorial_step_key == "mouse_mode"
        if not tutorial_demo_only:
            if update.cursor_position is not None:
                self.mouse_controller.move_normalized(*update.cursor_position)
            if update.left_press:
                self.mouse_controller.left_down()
            if update.left_release:
                self.mouse_controller.left_up()
            if update.left_click:
                self.mouse_controller.left_click()
            if update.right_click:
                self.mouse_controller.right_click()
            if update.scroll_steps:
                self.mouse_controller.scroll(update.scroll_steps)

        self._mouse_mode_enabled = update.mode_enabled
        self._mouse_status_text = update.status
        action_text = update.control_text
        if not tutorial_demo_only and (update.left_press or update.left_release or update.left_click or update.right_click or update.scroll_steps):
            action_text = self.mouse_controller.message
            self.command_detected.emit(action_text)
        self._mouse_control_text = action_text
        return update.consume_other_routes

    def _update_volume_overlay(self) -> None:
        msg = self._volume_message if self._volume_mode_active else self._volume_status_text
        if self._volume_dual_active:
            self.volume_overlay.set_dual_level(
                self._volume_app_level,
                self._volume_level,
                self._volume_app_label,
                muted=self._volume_muted,
                active=self._volume_mode_active,
                selected_bar=self._volume_bar_selected,
                message=msg,
            )
        else:
            self.volume_overlay.set_level(
                self._volume_level,
                muted=self._volume_muted,
                active=self._volume_mode_active,
                message=msg,
            )
        if self._volume_overlay_visible:
            if not self.volume_overlay.isVisible():
                self.volume_overlay.show_overlay()
        elif self.volume_overlay.isVisible():
            self.volume_overlay.hide_overlay()

    def _read_system_mute(self) -> bool:
        muted = self.volume_controller.get_mute()
        return bool(muted) if muted is not None else False

    def _queue_spotify_volume(self, volume_percent: int) -> None:
        volume_percent = max(0, min(100, int(volume_percent)))
        with self._spotify_vol_lock:
            self._spotify_vol_target = volume_percent
            worker = self._spotify_vol_worker
            if worker is not None and worker.is_alive():
                return
            self._spotify_vol_worker = threading.Thread(
                target=self._spotify_vol_worker_loop,
                daemon=True,
            )
            self._spotify_vol_worker.start()

    def _spotify_vol_worker_loop(self) -> None:
        min_interval = 0.12
        while True:
            with self._spotify_vol_lock:
                target = self._spotify_vol_target
                last = self._spotify_vol_last_sent
                if target is None or target == last:
                    self._spotify_vol_worker = None
                    return
                self._spotify_vol_last_sent = target
            try:
                self.spotify_controller.set_volume(target)
            except Exception:
                pass
            time.sleep(min_interval)

    def _update_runtime_status(self) -> None:
        if self._dictation_active:
            self._emit_status("dictation active")
        elif self._voice_listening:
            self._emit_status("voice listening...")
        elif self._drawing_mode_enabled:
            self._emit_status("Touchless active | drawing mode on")
        elif self._chrome_mode_enabled:
            self._emit_status("Touchless active | chrome mode on")
        else:
            self._emit_status("Touchless active")

    def _emit_status(self, text: str) -> None:
        normalized = str(text or "").strip()
        if not normalized or normalized == self._last_status_text:
            return
        self._last_status_text = normalized
        self.status_changed.emit(normalized)

    def _build_debug_payload(self, result, now: float) -> dict:
        prediction = result.prediction
        dynamic_display = prediction.dynamic_label
        if prediction.dynamic_label != "neutral":
            self._dynamic_hold_label = prediction.dynamic_label
            self._dynamic_hold_until = now + 0.85
        elif now < self._dynamic_hold_until:
            dynamic_display = self._dynamic_hold_label
        else:
            self._dynamic_hold_label = "neutral"
            self._dynamic_hold_until = 0.0

        payload_raw_label = prediction.raw_label
        payload_stable_label = prediction.stable_label
        payload_dynamic_label = dynamic_display
        banner_text = prediction.stable_label if prediction.stable_label != "neutral" else prediction.raw_label
        if self._drawing_mode_enabled:
            payload_raw_label = "neutral"
            payload_stable_label = "neutral"
            payload_dynamic_label = "neutral"
            if self._drawing_tool == "draw":
                gesture_chip = "Drawing: draw"
            elif self._drawing_tool == "erase":
                gesture_chip = "Drawing: erase"
            elif self._drawing_tool == "hover":
                gesture_chip = "Drawing: hover"
            else:
                gesture_chip = "Drawing mode on"
        elif dynamic_display != "neutral":
            gesture_chip = f"Dynamic: {dynamic_display.replace('_', ' ')}"
        else:
            gesture_chip = f"Gesture: {banner_text}"

        handedness = ""
        if result.found and result.tracked_hand is not None:
            handedness = str(result.tracked_hand.handedness or "").lower()

        wheel_items = ()
        wheel_selected_key = None
        wheel_selection_progress = 0.0
        wheel_cursor_offset = None
        wheel_kind = "none"
        wheel_visible = False
        if self._chrome_wheel_visible:
            wheel_kind = "chrome"
            wheel_visible = True
            wheel_items = self._chrome_wheel_items()
            wheel_selected_key = self._chrome_wheel_selected_key
            wheel_cursor_offset = self._chrome_wheel_cursor_offset
            if self._chrome_wheel_selected_key is not None:
                wheel_selection_progress = min(1.0, max(0.0, (now - self._chrome_wheel_selected_since) / 1.0))
        elif self._spotify_wheel_visible:
            wheel_kind = "spotify"
            wheel_visible = True
            wheel_items = self._spotify_wheel_items()
            wheel_selected_key = self._spotify_wheel_selected_key
            wheel_cursor_offset = self._spotify_wheel_cursor_offset
            if self._spotify_wheel_selected_key is not None:
                wheel_selection_progress = min(1.0, max(0.0, (now - self._spotify_wheel_selected_since) / 1.0))

        info_lines = [
            f"Camera: {self._camera_info.display_name if self._camera_info is not None else 'waiting'}",
            "Handedness: -",
            f"Gesture raw/stable: {prediction.raw_label} / {prediction.stable_label}",
            f"Confidence: {prediction.confidence:.2f}",
            f"FPS: {self._fps:.1f}",
            "Box: -",
            "Palm: -",
            f"Dynamic: {dynamic_display.replace('_', ' ') if dynamic_display != 'neutral' else 'neutral'}",
            "Candidates: -",
            "Thumb: -",
            "Index: -",
            "Middle: -",
            "Ring: -",
            "Pinky: -",
            "Spreads: -",
            "Reasoning: no hand in frame",
            f"Volume control: {self._volume_message}",
            self._format_volume_level_text(),
            f"Spotify control: {self._spotify_control_text}",
            f"Spotify info: {self._spotify_info_text}",
            f"Chrome mode: {'on' if self._chrome_mode_enabled else 'off'}",
            f"Chrome control: {self._chrome_control_text}",
            f"YouTube mode: {self._youtube_mode_info}",
            f"YouTube control: {self._youtube_control_text}",
            f"Voice mode: {self._voice_mode_text()}",
            f"Voice control: {self._voice_control_text}",
            f"Voice heard: {self._voice_preview_text(self._voice_display_text)}",
            f"Mouse mode: {'on' if self._mouse_mode_enabled else 'off'} ({self._mouse_status_text})",
            f"Mouse control: {self._mouse_control_text}",
        ]
        if self._window_pair_overlay is not None:
            info_lines.append(f"Window pair distance: {float(self._window_pair_overlay.get('distance', 0.0) or 0.0):.2f}")
        info_lines.append(f"Gestures: {'on' if self._gestures_enabled else 'off'}")
        info_lines.append(f"Drawing: {'on' if self._drawing_mode_enabled else 'off'} | {self._drawing_control_text} | target={self._drawing_render_target}")

        if result.found and result.tracked_hand is not None and result.hand_reading is not None:
            hand = result.tracked_hand
            reading = result.hand_reading
            info_lines[1] = f"Handedness: {hand.handedness} ({hand.handedness_confidence:.2f})"
            info_lines[5] = (
                f"Box: x={hand.bbox.x:.2f} y={hand.bbox.y:.2f} "
                f"w={hand.bbox.width:.2f} h={hand.bbox.height:.2f}"
            )
            info_lines[6] = (
                f"Palm: roll={reading.palm.roll_deg:.1f} "
                f"pitch={reading.palm.pitch_deg:.1f} yaw={reading.palm.yaw_deg:.1f}"
            )
            info_lines[7] = f"Dynamic: {dynamic_display.replace('_', ' ') if dynamic_display != 'neutral' else 'neutral'}"
            candidate_text = ", ".join(
                f"{candidate.label}={candidate.score:.2f}" for candidate in prediction.candidates[:4]
            ) or "-"
            info_lines[8] = f"Candidates: {candidate_text}"
            for row, name in zip(range(9, 14), ("thumb", "index", "middle", "ring", "pinky")):
                finger = reading.fingers[name]
                info_lines[row] = (
                    f"{name.title()}: {finger.state} | "
                    f"open={finger.openness:.2f} curl={finger.curl:.2f} "
                    f"conf={finger.confidence:.2f} occ={finger.occluded}"
                )
            spread_text = ", ".join(
                f"{name}={spread.state}:{spread.distance:.2f}" for name, spread in reading.spreads.items()
            ) or "-"
            info_lines[14] = f"Spreads: {spread_text}"
            info_lines[15] = (
                f"Reasoning: extended={reading.finger_count_extended} "
                f"occlusion={reading.occlusion_score:.2f} shape={reading.shape_confidence:.2f}"
            )
        return {
            "result": result,
            "gesture_chip": gesture_chip,
            "info_lines": info_lines,
            "found": bool(result.found),
            "handedness": handedness,
            "raw_label": payload_raw_label,
            "stable_label": payload_stable_label,
            "dynamic_label": payload_dynamic_label,
            "confidence": float(prediction.confidence),
            "fps": float(self._fps),
            "low_fps_active": bool(self._low_fps_active),
            "low_fps_forced": bool(getattr(self.config, "low_fps_mode", False)),
            "low_fps_auto_engaged": bool(self._low_fps_auto_engaged),
            "force_ten_fps_test_mode": bool(getattr(self.config, "force_ten_fps_test_mode", False)),
            "spotify_window_open": bool(self.spotify_controller.is_window_active() or self.spotify_controller.is_running()),
            "spotify_control_text": self._spotify_control_text,
            "spotify_last_action": self._last_spotify_action,
            "chrome_mode_enabled": bool(self._chrome_mode_enabled),
            "chrome_window_active": bool(self.chrome_controller.is_window_active()),
            "chrome_window_open": bool(self.chrome_controller.is_window_open()),
            "chrome_control_text": self._chrome_control_text,
            "chrome_last_action": self._last_chrome_action,
            "voice_mode_text": self._voice_mode_text(),
            "voice_listening": bool(self._voice_listening),
            "voice_control_text": self._voice_control_text,
            "voice_heard_text": self._voice_preview_text(self._voice_display_text),
            "mouse_mode_enabled": bool(self._mouse_mode_enabled),
            "mouse_cursor_position": self.mouse_tracker.debug_state.cursor_position,
            "mouse_left_click": bool(getattr(self._last_mouse_update, "left_click", False)),
            "gestures_enabled": bool(self._gestures_enabled),
            "drawing_mode_enabled": bool(self._drawing_mode_enabled),
            "drawing_tool": self._drawing_tool,
            "drawing_cursor_norm": self._drawing_cursor_norm,
            "drawing_control_text": self._drawing_control_text,
            "drawing_render_target": self._drawing_render_target,
            "drawing_request_token": int(self._drawing_request_token),
            "drawing_request_action": self._drawing_request_action,
            "drawing_shape_mode": bool(self._drawing_shape_mode),
            "utility_request_token": int(self._utility_request_token),
            "utility_request_action": self._utility_request_action,
            "utility_capture_selection_active": bool(self._utility_capture_selection_active),
            "utility_capture_cursor_norm": self._utility_capture_cursor_norm,
            "utility_capture_left_down": bool(self._utility_capture_left_down),
            "utility_capture_right_down": bool(self._utility_capture_right_down),
            "utility_recording_active": bool(self._utility_recording_active),
            "wheel_kind": wheel_kind,
            "wheel_visible": wheel_visible,
            "wheel_items": wheel_items,
            "wheel_selected_key": wheel_selected_key,
            "wheel_selection_progress": wheel_selection_progress,
            "wheel_cursor_offset": wheel_cursor_offset,
            "volume_level_scalar": self._volume_level,
            "volume_muted": self._volume_muted,
            "volume_active": self._volume_mode_active,
        }

    def _format_volume_level_text(self) -> str:
        mute_suffix = " [muted]" if self._volume_muted else ""
        if self._volume_level is None:
            return f"Volume level: -   ({self._volume_status_text}{mute_suffix})"
        return f"Volume level: {int(round(self._volume_level * 100))}%   ({self._volume_status_text}{mute_suffix})"

    def _volume_features_from_hand_reading(self, hand_reading):
        fine_states = {name: finger.state for name, finger in hand_reading.fingers.items()}
        states = {
            name: ("open" if finger.state in {"fully_open", "partially_curled"} else "closed")
            for name, finger in hand_reading.fingers.items()
        }
        open_scores = {name: float(finger.openness) for name, finger in hand_reading.fingers.items()}
        spread_states = {name: spread.state for name, spread in hand_reading.spreads.items()}
        spread_ratios = {name: float(spread.distance) for name, spread in hand_reading.spreads.items()}
        spread_together_strengths = {name: float(spread.together_strength) for name, spread in hand_reading.spreads.items()}
        spread_apart_strengths = {name: float(spread.apart_strength) for name, spread in hand_reading.spreads.items()}
        return SimpleNamespace(
            palm_scale=float(hand_reading.palm.scale),
            open_scores=open_scores,
            states=states,
            fine_states=fine_states,
            finger_count_open=sum(1 for finger in hand_reading.fingers.values() if finger.extended),
            spread_states=spread_states,
            spread_ratios=spread_ratios,
            spread_together_strengths=spread_together_strengths,
            spread_apart_strengths=spread_apart_strengths,
        )

    def _derive_app_static_label(self, prediction, hand_reading):
        stable_label = prediction.stable_label
        if hand_reading is None:
            return stable_label
        if stable_label in {"three", "neutral"} and self._is_three_apart(hand_reading):
            return "three_apart"
        if stable_label in {"three", "neutral"} and self._is_three_together(hand_reading):
            return "three_together"
        if stable_label in {"four", "neutral"} and self._is_four_together(hand_reading):
            return "four_together"
        return stable_label

    def _is_three_together(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            self._is_chrome_open_finger(fingers["index"])
            and self._is_chrome_open_finger(fingers["middle"])
            and self._is_chrome_open_finger(fingers["ring"])
            and self._is_folded_finger(fingers["thumb"], allow_partial=True)
            and self._is_folded_finger(fingers["pinky"])
            and self._spread_is_together(hand_reading, "index_middle", max_distance=0.38, min_strength=0.56)
            and self._spread_is_together(hand_reading, "middle_ring", max_distance=0.38, min_strength=0.54)
        )

    def _is_three_apart(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            self._is_chrome_open_finger(fingers["index"])
            and self._is_chrome_open_finger(fingers["middle"])
            and self._is_chrome_open_finger(fingers["ring"])
            and self._is_folded_finger(fingers["thumb"], allow_partial=True)
            and self._is_folded_finger(fingers["pinky"])
            and self._spread_is_apart(hand_reading, "index_middle", min_distance=0.50, min_strength=0.56)
            and self._spread_is_apart(hand_reading, "middle_ring", min_distance=0.48, min_strength=0.54)
        )

    def _is_four_together(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            all(self._is_chrome_open_finger(fingers[name]) for name in ("index", "middle", "ring", "pinky"))
            and self._is_folded_finger(fingers["thumb"], allow_partial=True)
            and self._spread_is_together(hand_reading, "index_middle", max_distance=0.40, min_strength=0.52)
            and self._spread_is_together(hand_reading, "middle_ring", max_distance=0.40, min_strength=0.52)
            and self._spread_is_together(hand_reading, "ring_pinky", max_distance=0.40, min_strength=0.52)
        )

    def _is_chrome_open_finger(self, finger) -> bool:
        return finger.state == "fully_open" or (
            finger.state == "partially_curled"
            and finger.openness >= 0.70
            and finger.curl <= 0.46
        )

    def _is_folded_finger(self, finger, *, allow_partial: bool = False) -> bool:
        allowed = {"mostly_curled", "closed"}
        if allow_partial:
            allowed.add("partially_curled")
        return finger.state in allowed

    def _spread_is_together(self, hand_reading, spread_name: str, *, max_distance: float, min_strength: float) -> bool:
        spread = hand_reading.spreads[spread_name]
        return (
            spread.state == "together"
            or spread.distance <= max_distance
            or (spread.together_strength >= min_strength and spread.apart_strength <= 0.42)
        )

    def _spread_is_apart(self, hand_reading, spread_name: str, *, min_distance: float, min_strength: float) -> bool:
        spread = hand_reading.spreads[spread_name]
        return (
            spread.state == "apart"
            or spread.distance >= min_distance
            or (spread.apart_strength >= min_strength and spread.together_strength <= 0.42)
        )

    def _reset_spotify_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._spotify_wheel_candidate = "neutral"
        self._spotify_wheel_candidate_since = 0.0
        self._spotify_wheel_visible = False
        self._spotify_wheel_anchor = None
        self._spotify_wheel_selected_key = None
        self._spotify_wheel_selected_since = 0.0
        self._spotify_wheel_pose_grace_until = 0.0
        self._spotify_wheel_cursor_offset = None
        if clear_cooldown:
            self._spotify_wheel_cooldown_until = 0.0
        if self.spotify_wheel_overlay.isVisible():
            self.spotify_wheel_overlay.hide_overlay()

    def _reset_chrome_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._chrome_wheel_candidate = "neutral"
        self._chrome_wheel_candidate_since = 0.0
        self._chrome_wheel_visible = False
        self._chrome_wheel_anchor = None
        self._chrome_wheel_selected_key = None
        self._chrome_wheel_selected_since = 0.0
        self._chrome_wheel_pose_grace_until = 0.0
        self._chrome_wheel_cursor_offset = None
        if clear_cooldown:
            self._chrome_wheel_cooldown_until = 0.0
        if self.chrome_wheel_overlay.isVisible():
            self.chrome_wheel_overlay.hide_overlay()

    def _reset_youtube_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._youtube_wheel_candidate = "neutral"
        self._youtube_wheel_candidate_since = 0.0
        self._youtube_wheel_visible = False
        self._youtube_wheel_anchor = None
        self._youtube_wheel_selected_key = None
        self._youtube_wheel_selected_since = 0.0
        self._youtube_wheel_pose_grace_until = 0.0
        self._youtube_wheel_cursor_offset = None
        if clear_cooldown:
            self._youtube_wheel_cooldown_until = 0.0
        if self.youtube_wheel_overlay.isVisible():
            self.youtube_wheel_overlay.hide_overlay()

    def _youtube_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        labels = (
            ("fullscreen", "Fullscreen"),
            ("theater", "Theater"),
            ("mini_player", "Mini Player"),
            ("captions", "Captions"),
            ("like", "Like"),
            ("dislike", "Dislike"),
            ("share", "Share"),
            ("speed_down", "Slower"),
            ("speed_up", "Faster"),
        )
        slice_span = 360.0 / len(labels)
        return tuple(
            (key, label, (90.0 - index * slice_span) % 360.0)
            for index, (key, label) in enumerate(labels)
        )

    def _youtube_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._youtube_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _youtube_wheel_pose_active(self, prediction) -> bool:
        if prediction is None:
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        if stable_label == "wheel_pose":
            return True
        if raw_label == "wheel_pose" and confidence >= 0.48:
            return True
        return False

    def _update_youtube_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        youtube_active = self._youtube_mode_info in {"forced", "auto"}
        if not active or prediction is None or hand_reading is None or not youtube_active:
            if self._youtube_wheel_visible and now >= self._youtube_wheel_pose_grace_until:
                self._youtube_control_text = "youtube wheel closed"
                self._reset_youtube_wheel()
            else:
                self._youtube_wheel_candidate = "neutral"
                self._youtube_wheel_candidate_since = now
            return self._youtube_wheel_visible

        wheel_pose = self._youtube_wheel_pose_active(prediction)
        if self._youtube_wheel_visible:
            if wheel_pose:
                self._youtube_wheel_pose_grace_until = now + 0.25
                self._update_youtube_wheel_selection(hand_reading, now)
            elif now >= self._youtube_wheel_pose_grace_until:
                self._youtube_control_text = "youtube wheel closed"
                self._reset_youtube_wheel()
            return True

        if now < self._youtube_wheel_cooldown_until:
            if not wheel_pose:
                self._youtube_wheel_candidate = "neutral"
            return False

        if not wheel_pose:
            self._youtube_wheel_candidate = "neutral"
            self._youtube_wheel_candidate_since = now
            return False

        if self._youtube_wheel_candidate != "youtube_wheel_pose":
            self._youtube_wheel_candidate = "youtube_wheel_pose"
            self._youtube_wheel_candidate_since = now
            self._youtube_control_text = "hold youtube wheel pose"
            return True

        if now - self._youtube_wheel_candidate_since < 1.0:
            return True

        self._youtube_wheel_visible = True
        self._youtube_wheel_anchor = hand_reading.palm.center.copy()
        self._youtube_wheel_cursor_offset = (0.0, 0.0)
        self._youtube_wheel_selected_key = None
        self._youtube_wheel_selected_since = now
        self._youtube_wheel_pose_grace_until = now + 0.25
        self._youtube_control_text = "youtube wheel active"
        return True

    def _update_youtube_wheel_selection(self, hand_reading, now: float) -> None:
        if self._youtube_wheel_anchor is None:
            self._youtube_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._youtube_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._youtube_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._youtube_wheel_items())
        if selection_key is None:
            self._youtube_wheel_selected_key = None
            self._youtube_wheel_selected_since = now
            self._youtube_control_text = "youtube wheel active"
            return
        if selection_key != self._youtube_wheel_selected_key:
            self._youtube_wheel_selected_key = selection_key
            self._youtube_wheel_selected_since = now
            self._youtube_control_text = f"youtube wheel: {self._youtube_wheel_label(selection_key)}"
            return
        if now - self._youtube_wheel_selected_since < 1.0:
            return
        self._execute_youtube_wheel_action(selection_key, now)
        self._youtube_wheel_cooldown_until = now + 1.5
        self._reset_youtube_wheel()

    def _execute_youtube_wheel_action(self, key: str, now: float) -> None:
        controller = self.youtube_controller
        if key == "fullscreen":
            controller.toggle_fullscreen()
        elif key == "theater":
            controller.toggle_theater()
        elif key == "mini_player":
            controller.toggle_mini_player()
        elif key == "captions":
            controller.toggle_captions()
        elif key == "like":
            controller.like_video()
        elif key == "dislike":
            controller.dislike_video()
        elif key == "share":
            controller.share_video()
            self.mouse_tracker.force_enable_mode(now)
            self._mouse_mode_enabled = True
            self._mouse_control_text = "mouse mode on"
            self.voice_status_overlay.show_info_hint("Mouse mode on - click the Share options", duration=3.0)
        elif key == "speed_down":
            controller.speed_down()
        elif key == "speed_up":
            controller.speed_up()
        else:
            return
        self._youtube_control_text = controller.message
        action_key = f"youtube_{key}"
        if key == "captions" and "no captions available" in controller.message.lower():
            action_key = "youtube_captions_unavailable"
            self.voice_status_overlay.show_info_hint("No captions available for this video", duration=3.0)
        self.command_detected.emit(self._youtube_control_text)
        self._record_action(action_key, self._youtube_control_text)

    def _spotify_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        labels = (
            ("add_playlist", "Add Playlist"),
            ("remove_playlist", "Remove Playlist"),
            ("create_playlist", "Create Playlist"),
            ("add_queue", "Add Queue"),
            ("remove_queue", "Remove Queue"),
            ("like", "Add to Liked"),
            ("remove_liked", "Remove from Liked"),
            ("shuffle", "Shuffle"),
        )
        slice_span = 360.0 / len(labels)
        return tuple(
            (key, label, (90.0 - index * slice_span) % 360.0)
            for index, (key, label) in enumerate(labels)
        )

    def _chrome_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        return (
            ("bookmark", "Bookmark This Tab", 90.0),
            ("history", "History", 30.0),
            ("downloads", "Downloads", 330.0),
            ("bookmarks", "Bookmarks Manager", 270.0),
            ("print", "Print Page", 210.0),
            ("reopen", "Reopen Tab", 150.0),
        )

    def _chrome_active_for_wheel(self, now: float) -> bool:
        if now < self._chrome_active_cache_until:
            return self._chrome_active_cache
        active = bool(self.chrome_controller.is_window_active())
        self._chrome_active_cache = active
        self._chrome_active_cache_until = now + 0.55
        return active

    def _spotify_active_for_wheel(self, now: float) -> bool:
        if now < self._spotify_active_cache_until:
            return self._spotify_active_cache
        if hasattr(self.spotify_controller, "is_active_for_wheel"):
            active = bool(self.spotify_controller.is_active_for_wheel())
        else:
            active = bool(self.spotify_controller.is_active_device_available())
        self._spotify_active_cache = active
        self._spotify_active_cache_until = now + 0.9
        return active

    def _chrome_wheel_pose_active(self, prediction, now: float) -> bool:
        if prediction is None:
            return False
        if self._youtube_mode_info in {"forced", "auto"}:
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        if stable_label == "chrome_wheel_pose":
            return True
        if raw_label == "chrome_wheel_pose" and confidence >= 0.50:
            return True
        if self._chrome_mode_enabled and self._chrome_active_for_wheel(now):
            if stable_label == "wheel_pose":
                return True
            if raw_label == "wheel_pose" and confidence >= 0.48:
                return True
        return False

    def _update_chrome_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._chrome_wheel_visible and now >= self._chrome_wheel_pose_grace_until:
                self._chrome_control_text = "chrome wheel closed"
                self._reset_chrome_wheel()
            else:
                self._chrome_wheel_candidate = "neutral"
                self._chrome_wheel_candidate_since = now
            return self._chrome_wheel_visible

        wheel_label = self._chrome_wheel_pose_active(prediction, now)
        if self._chrome_wheel_visible:
            if wheel_label:
                self._chrome_wheel_pose_grace_until = now + 0.25
                self._update_chrome_wheel_selection(hand_reading, now)
            elif now >= self._chrome_wheel_pose_grace_until:
                self._chrome_control_text = "chrome wheel closed"
                self._reset_chrome_wheel()
            return True

        if now < self._chrome_wheel_cooldown_until:
            if not wheel_label:
                self._chrome_wheel_candidate = "neutral"
            return False

        if not wheel_label:
            self._chrome_wheel_candidate = "neutral"
            self._chrome_wheel_candidate_since = now
            return False

        if not self._chrome_active_for_wheel(now):
            self._chrome_control_text = "chrome must be active for wheel"
            self._chrome_wheel_candidate = "neutral"
            self._chrome_wheel_candidate_since = now
            return True

        if self._chrome_wheel_candidate != "chrome_wheel_pose":
            self._chrome_wheel_candidate = "chrome_wheel_pose"
            self._chrome_wheel_candidate_since = now
            self._chrome_control_text = "hold chrome wheel pose"
            return True

        if now - self._chrome_wheel_candidate_since < 1.0:
            return True

        self._chrome_wheel_visible = True
        self._chrome_wheel_anchor = hand_reading.palm.center.copy()
        self._chrome_wheel_cursor_offset = (0.0, 0.0)
        self._chrome_wheel_selected_key = None
        self._chrome_wheel_selected_since = now
        self._chrome_wheel_pose_grace_until = now + 0.25
        self._chrome_control_text = "chrome wheel active"
        return True

    def _update_chrome_wheel_selection(self, hand_reading, now: float) -> None:
        if self._chrome_wheel_anchor is None:
            self._chrome_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._chrome_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._chrome_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._chrome_wheel_items())
        if selection_key is None:
            self._chrome_wheel_selected_key = None
            self._chrome_wheel_selected_since = now
            self._chrome_control_text = "chrome wheel active"
            return
        if selection_key != self._chrome_wheel_selected_key:
            self._chrome_wheel_selected_key = selection_key
            self._chrome_wheel_selected_since = now
            self._chrome_control_text = f"chrome wheel: {self._chrome_wheel_label(selection_key)}"
            return
        if now - self._chrome_wheel_selected_since < 1.0:
            return
        self._execute_chrome_wheel_action(selection_key)
        self._chrome_wheel_cooldown_until = now + 1.5
        self._reset_chrome_wheel()

    def _wheel_selection_key(self, dx: float, dy: float, items: tuple[tuple[str, str, float], ...]) -> str | None:
        radius = math.hypot(dx, dy)
        if radius < 0.59 or radius > 1.25:
            return None
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        slice_span = 360.0 / max(1, len(items))
        half_slice = slice_span / 2.0
        angular_margin = 1.5
        for key, _label, target_angle in items:
            delta = abs((angle - target_angle + 180.0) % 360.0 - 180.0)
            if delta <= max(0.0, half_slice - angular_margin):
                return key
        return None

    def _chrome_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._chrome_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _execute_chrome_wheel_action(self, key: str) -> None:
        if key == "bookmark":
            success = self.chrome_controller.bookmark_current_tab()
        elif key == "history":
            success = self.chrome_controller.open_history()
        elif key == "downloads":
            success = self.chrome_controller.open_downloads()
        elif key == "bookmarks":
            success = self.chrome_controller.open_bookmarks_manager()
        elif key == "print":
            success = self.chrome_controller.print_page()
        else:
            success = self.chrome_controller.reopen_closed_tab()
        self._chrome_control_text = self.chrome_controller.message
        self.command_detected.emit(self._chrome_control_text)
        if success:
            self._chrome_mode_enabled = True

    def _update_spotify_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._spotify_wheel_visible and now >= self._spotify_wheel_pose_grace_until:
                self._spotify_control_text = "spotify wheel closed"
                self._reset_spotify_wheel()
            else:
                self._spotify_wheel_candidate = "neutral"
                self._spotify_wheel_candidate_since = now
            return self._spotify_wheel_visible

        wheel_label = prediction.stable_label == "wheel_pose" or (
            prediction.raw_label == "wheel_pose" and prediction.confidence >= 0.56
        )
        if self._spotify_wheel_visible:
            if wheel_label:
                self._spotify_wheel_pose_grace_until = now + 0.25
                self._update_spotify_wheel_selection(hand_reading, now)
            elif now >= self._spotify_wheel_pose_grace_until:
                self._spotify_control_text = "spotify wheel closed"
                self._reset_spotify_wheel()
            return True

        if now < self._spotify_wheel_cooldown_until:
            if not wheel_label:
                self._spotify_wheel_candidate = "neutral"
            return False

        if self._chrome_mode_enabled and self._chrome_active_for_wheel(now):
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return False

        if not wheel_label:
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return False

        if not self._spotify_active_for_wheel(now):
            self._spotify_control_text = self.spotify_controller.message or "spotify inactive on device"
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return True

        if self._spotify_wheel_candidate != "wheel_pose":
            self._spotify_wheel_candidate = "wheel_pose"
            self._spotify_wheel_candidate_since = now
            self._spotify_control_text = "hold wheel pose for spotify wheel"
            return True

        if now - self._spotify_wheel_candidate_since < 1.0:
            return True

        self._spotify_wheel_visible = True
        self._spotify_wheel_anchor = hand_reading.palm.center.copy()
        self._spotify_wheel_cursor_offset = (0.0, 0.0)
        self._spotify_wheel_selected_key = None
        self._spotify_wheel_selected_since = now
        self._spotify_wheel_pose_grace_until = now + 0.25
        self._spotify_control_text = "spotify wheel active"
        return True

    def _update_spotify_wheel_selection(self, hand_reading, now: float) -> None:
        if self._spotify_wheel_anchor is None:
            self._spotify_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._spotify_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._spotify_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._spotify_wheel_selection_key(float(offset[0]), float(offset[1]))
        if selection_key is None:
            self._spotify_wheel_selected_key = None
            self._spotify_wheel_selected_since = now
            self._spotify_control_text = "spotify wheel active"
            return
        if selection_key != self._spotify_wheel_selected_key:
            self._spotify_wheel_selected_key = selection_key
            self._spotify_wheel_selected_since = now
            self._spotify_control_text = f"spotify wheel: {self._spotify_wheel_label(selection_key)}"
            return
        if now - self._spotify_wheel_selected_since < 1.0:
            return
        self._execute_spotify_wheel_action(selection_key)
        self._spotify_wheel_cooldown_until = now + 1.5
        self._reset_spotify_wheel()

    def _spotify_wheel_selection_key(self, dx: float, dy: float) -> str | None:
        return self._wheel_selection_key(dx, dy, self._spotify_wheel_items())

    def _spotify_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._spotify_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _execute_spotify_wheel_action(self, key: str) -> None:
        if key == "add_playlist":
            self._spotify_control_text = "say what playlist you would like to add to"
            self.command_detected.emit(self._spotify_control_text)
            self._start_playlist_prompt("add_playlist")
            return
        if key == "create_playlist":
            self._spotify_control_text = "what would you like to call the playlist?"
            self.command_detected.emit(self._spotify_control_text)
            self._start_playlist_prompt("create_playlist")
            return
        if key == "remove_playlist":
            success = self.spotify_controller.remove_current_track_from_current_playlist()
            self._spotify_control_text = self.spotify_controller.message
            self.command_detected.emit(self._spotify_control_text)
            if success:
                details = self.spotify_controller.get_current_track_details()
                if details is not None:
                    self._spotify_info_text = details.summary()
            return
        if key == "add_queue":
            success = self.spotify_controller.add_current_track_to_queue()
        elif key == "remove_queue":
            success = self.spotify_controller.remove_current_track_from_queue()
        elif key == "remove_liked":
            success = self.spotify_controller.remove_current_track_from_liked()
        elif key == "shuffle":
            success = self.spotify_controller.toggle_shuffle()
        else:
            success = self.spotify_controller.save_current_track()
        self._spotify_control_text = self.spotify_controller.message
        self.command_detected.emit(self._spotify_control_text)
        if success:
            details = self.spotify_controller.get_current_track_details()
            if details is not None:
                self._spotify_info_text = details.summary()

    def _start_playlist_prompt(self, action: str) -> None:
        if self._voice_listening:
            return
        self._voice_cooldown_until = time.monotonic() + 0.7
        self._start_voice_capture(mode=action, preferred_app=None)

    def _clean_playlist_reply(self, spoken_text: str) -> str:
        cleaned = str(spoken_text or "").lower()
        cleaned = cleaned.replace("feel good", "feel-good")
        cleaned = re.sub(
            r"\b(and|then|uh|um|add|remove|it|this|current|song|track|playlist|to|from|my|the|please|thanks|thank you|would like|like|called|named|titled)\b",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
        return cleaned

    def _clean_create_playlist_reply(self, spoken_text: str) -> str:
        cleaned = str(spoken_text or "").strip()
        cleaned = re.sub(
            r"^\s*(please\s+)?(can you\s+)?(create|make|new|build|set up)\s+(a\s+)?(new\s+)?(playlist\s+)?(called|named|titled)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(uh|um|please|thanks|thank you)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
        words = [word.capitalize() if word.islower() else word for word in cleaned.split(" ") if word]
        return " ".join(words)

    def _handle_left_hand_voice(self, prediction, now: float) -> None:
        stable_label = prediction.stable_label
        trigger_labels = {"one", "two"}

        if stable_label == "fist":
            # Left-fist also dismisses the low-FPS suggestion toast when it's up.
            # Cheap no-op when the overlay is already hidden, so this is safe
            # to call ahead of the existing voice-cancel logic.
            self._dismiss_low_fps_suggestion_via_gesture()
            if self._voice_latched_label == "fist":
                return
            if now - float(getattr(self, "_voice_one_two_triggered_at", 0.0) or 0.0) < 1.0:
                return
            if self._voice_listening or self._dictation_active or self._save_prompt_active or self._selection_prompt_active:
                self._cancel_all_voice_stages()
                self._voice_latched_label = "fist"
                self._voice_cooldown_until = now + 0.8
            return
        if self._voice_latched_label == "fist":
            self._voice_latched_label = None

        if stable_label == self._voice_latched_label:
            if stable_label not in trigger_labels:
                self._voice_latched_label = None
            return

        if stable_label not in trigger_labels:
            if self._dictation_active and self._dictation_toggle_release_required:
                if self._dictation_release_candidate_since <= 0.0:
                    self._dictation_release_candidate_since = now
                elif now - self._dictation_release_candidate_since >= 0.35:
                    self._dictation_toggle_release_required = False
            else:
                self._dictation_release_candidate_since = 0.0
            self._reset_voice_candidate(now)
            return

        self._dictation_release_candidate_since = 0.0

        if stable_label != self._voice_candidate:
            self._voice_candidate = stable_label
            self._voice_candidate_since = now
            return

        if stable_label == "one" and (self._voice_listening or self._dictation_active):
            return
        if stable_label == "two" and self._dictation_active and self._dictation_toggle_release_required:
            return
        if stable_label == "two" and self._dictation_active and now < self._dictation_stop_rearm_at:
            return
        if now < self._voice_cooldown_until:
            return
        if now - self._voice_candidate_since < 0.5:
            return

        self._voice_latched_label = stable_label
        self._voice_cooldown_until = now + 1.25
        self._voice_one_two_triggered_at = now
        if stable_label == "two":
            if self._dictation_active:
                self._stop_dictation_capture()
            else:
                self._start_dictation_capture()
            return
        if self._selection_prompt_active:
            self._start_voice_capture(mode="selection", preferred_app=None)
            return
        self._start_voice_command()

    def _reset_voice_candidate(self, now: float) -> None:
        self._voice_candidate = "neutral"
        self._voice_candidate_since = now
        if self._voice_latched_label is not None:
            self._voice_latched_label = None

    def _voice_mode_text(self) -> str:
        if self._dictation_active:
            return "dictation"
        if self._voice_listening:
            if self._voice_mode == "general":
                return "command"
            return self._voice_mode.replace("_", " ")
        return "ready"

    def _voice_preview_text(self, text: str, *, max_chars: int = 220) -> str:
        value = str(text or "").strip()
        if not value:
            return "-"
        if len(value) <= max_chars:
            return value
        return "..." + value[-(max_chars - 3):]

    def _start_voice_command(self) -> None:
        chrome_mode_voice = self._chrome_mode_enabled and self.chrome_controller.is_window_open()
        self._start_voice_capture(mode="general", preferred_app="chrome" if chrome_mode_voice else None)

    def _start_dictation_capture(self) -> None:
        if self._voice_listening or self._dictation_active:
            return
        # Latch the external (non-HGR) window the user had focused before
        # making the gesture -- that's where dictated text must land. Must
        # happen BEFORE any overlay shows, because the overlay may pull
        # foreground briefly.
        try:
            self.text_input_controller.capture_target_window()
        except Exception:
            pass
        # Short toggle-off rearm so a second "two" stops dictation
        # without feeling laggy. The release-required gate + candidate
        # hold still prevent instant accidental re-toggles.
        self._dictation_toggle_release_required = True
        self._dictation_release_candidate_since = 0.0
        self._dictation_stop_rearm_at = time.monotonic() + 0.9
        # Route through the proven voice-capture pipeline: whisper.cpp
        # (or faster-whisper) -> text_input_controller.insert_text.
        # That path is what the rest of the app's voice features use
        # and is known to actually type into the target window.
        self._start_voice_capture(mode="dictation")

    def _stop_dictation_capture(self) -> None:
        if not self._dictation_active:
            return
        # Signal the dictation worker to break out of its listen loop.
        if self._voice_stop_event is not None:
            try:
                self._voice_stop_event.set()
            except Exception:
                pass
        self._dictation_active = False
        self._dictation_toggle_release_required = False
        self._dictation_release_candidate_since = 0.0
        self._dictation_stop_rearm_at = 0.0
        self._voice_listening = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_control_text = "dictation stopped"
        # Flush any pending buffered final before tearing state down.
        try:
            flush = self._dictation_state.get("_flush_pending") if self._dictation_state else None
            if callable(flush):
                flush()
        except Exception:
            pass
        try:
            self.grammar_corrector.stop()
        except Exception:
            pass
        try:
            if self.whisper_refiner is not None:
                self.whisper_refiner.stop()
        except Exception:
            pass
        self._dictation_state = None
        # Hide the mic indicator silently — no result popup.
        try:
            self.voice_status_overlay.hide_overlay()
        except Exception:
            pass

    def _start_voice_capture(self, *, mode: str, preferred_app: str | None = None) -> None:
        if self._voice_listening:
            return
        self._voice_listening = True
        self._voice_mode = mode
        self._voice_request_id += 1
        request_id = self._voice_request_id
        self._voice_stop_event = threading.Event() if mode == "dictation" else None
        self._dictation_active = mode == "dictation"
        self._dictation_backend = "whisper_loop" if mode == "dictation" else "idle"
        if mode == "dictation":
            self._voice_control_text = "dictation listening..."
        elif mode == "save_prompt":
            self._voice_control_text = self._save_prompt_text
        else:
            self._voice_control_text = "voice listening..."
        self._voice_heard_text = "-"
        self._voice_display_text = "-"

        if mode == "dictation":
            stop_event = self._voice_stop_event
            self.grammar_corrector.set_callback(self._apply_grammar_correction)
            self.grammar_corrector.start()
            if self.whisper_refiner is not None:
                try:
                    self.whisper_refiner.set_callback(self._apply_refinement)
                    started = self.whisper_refiner.start()
                    print(f"[hgr] whisper_refiner.start -> {started} ({self.whisper_refiner.message})")
                except Exception as exc:
                    print(f"[hgr] whisper_refiner start failed: {exc}")
            self.voice_status_overlay.show_processing("Preparing Dictation Mode")
            self.command_detected.emit(self._voice_control_text)
            self._emit_status("dictation active")

            def _dictation_worker() -> None:
                final_message = "dictation stopped"
                hold_back = 2
                _PENDING_WINDOW_SECONDS = 2.5
                state = {
                    "committed": "",
                    "last_words": [],
                    "final_display": self.dictation_processor.full_text,
                    "last_final": "",
                    "recent_finals": [],
                    "last_final_text": "",
                    "last_final_typed_len": 0,
                    "last_final_time": 0.0,
                    "stream_hyp_chars": 0,
                    "last_final_refined": False,
                    "pending_final_text": None,
                    "pending_final_timer": None,
                    "last_hypothesis_time": 0.0,
                }
                pending_lock = threading.Lock()
                self._dictation_state = state

                def _common_prefix(a: list[str], b: list[str]) -> list[str]:
                    out: list[str] = []
                    for x, y in zip(a, b):
                        if x == y:
                            out.append(x)
                        else:
                            break
                    return out

                def _commit_final(text: str) -> None:
                    normalized = " ".join(text.lower().split())
                    now = time.monotonic()
                    prev_final_text = state.get("last_final_text", "")
                    last_final_time = state.get("last_final_time", 0.0)
                    time_gap = (now - last_final_time) if last_final_time > 0 else 999.0
                    no_new_audio = last_final_time > 0 and time_gap < 2.0
                    if (
                        normalized
                        and normalized in state["recent_finals"]
                        and no_new_audio
                    ):
                        print(f"[dictation] commit: dropped re-emission (gap={time_gap:.1f}s) {text!r}")
                        state["committed"] = ""
                        state["last_words"] = []
                        state["stream_hyp_chars"] = 0
                        return
                    redecode_done = False
                    overlap_hit = bool(prev_final_text) and _redecode_overlap(prev_final_text, text)
                    print(f"[dictation] commit: text={text!r} prev={prev_final_text!r} gap={time_gap:.2f}s overlap={overlap_hit}")
                    if (
                        prev_final_text
                        and time_gap < 10.0
                        and overlap_hit
                    ):
                        revert_chars = state.get("last_final_typed_len", 0) + state.get("stream_hyp_chars", 0)
                        if 0 < revert_chars <= 300:
                            to_type = text + " "
                            with self._corrector_lock:
                                removed = self.text_input_controller.remove_text(revert_chars)
                                if removed:
                                    inserted = self.text_input_controller.insert_text(to_type)
                                    if inserted:
                                        self.grammar_corrector.sync_replace(revert_chars, to_type)
                                        print(f"[dictation] re-decode replace: -{revert_chars} +{len(to_type)} chars")
                                        redecode_done = True

                    if redecode_done:
                        current_display = state["final_display"] or ""
                        prev_norm = " ".join(prev_final_text.lower().split())
                        if prev_norm and current_display.lower().endswith(prev_norm):
                            current_display = current_display[: len(current_display) - len(prev_final_text)].rstrip()
                        state["final_display"] = (current_display + " " + text).strip() if current_display else text
                        state["last_final_text"] = text
                        state["last_final_typed_len"] = len(text + " ")
                        state["last_final_time"] = now
                        state["last_final_refined"] = False
                        state["stream_hyp_chars"] = 0
                        state["committed"] = ""
                        state["last_words"] = []
                        state["last_final"] = normalized
                        if state["recent_finals"]:
                            state["recent_finals"][-1] = normalized
                        else:
                            state["recent_finals"].append(normalized)
                        self._voice_queue.put(
                            (
                                request_id,
                                {
                                    "event": "dictation_chunk",
                                    "success": True,
                                    "heard_text": text,
                                    "control_text": "dictation typing",
                                    "display_text": state["final_display"],
                                    "partial": False,
                                },
                            )
                        )
                        return

                    committed = state["committed"]
                    committed_chars = state.get("stream_hyp_chars", 0)
                    if text.startswith(committed):
                        remainder = text[len(committed):]
                    else:
                        remainder = text
                    to_type = remainder + " "
                    with self._corrector_lock:
                        self.text_input_controller.insert_text(to_type)
                        self.grammar_corrector.append(to_type)
                    current_display = state["final_display"] or ""
                    state["final_display"] = (current_display + " " + text).strip() if current_display else text
                    state["last_final_text"] = text
                    state["last_final_typed_len"] = committed_chars + len(to_type)
                    state["last_final_time"] = now
                    state["last_final_refined"] = False
                    state["stream_hyp_chars"] = 0
                    state["committed"] = ""
                    state["last_words"] = []
                    state["last_final"] = normalized
                    state["recent_finals"].append(normalized)
                    if len(state["recent_finals"]) > 4:
                        state["recent_finals"] = state["recent_finals"][-4:]
                    self._voice_queue.put(
                        (
                            request_id,
                            {
                                "event": "dictation_chunk",
                                "success": True,
                                "heard_text": text,
                                "control_text": "dictation typing",
                                "display_text": state["final_display"],
                                "partial": False,
                            },
                        )
                    )

                def _flush_pending() -> None:
                    with pending_lock:
                        text = state.get("pending_final_text")
                        state["pending_final_text"] = None
                        state["pending_final_timer"] = None
                    if text:
                        try:
                            _commit_final(text)
                        except Exception as exc:
                            print(f"[dictation] _flush_pending error: {exc}")

                def _buffer_final(text: str) -> None:
                    with pending_lock:
                        pending = state.get("pending_final_text")
                        normalized_new = " ".join(text.lower().split())
                        # Drop whisper's buffered re-emission of a just-
                        # committed chunk. With commit-only decoding the
                        # streamer cannot replay old audio, so "re-emission"
                        # collapses to "two identical commits within a short
                        # window" — anything past that is a legitimate user
                        # repeat.
                        last_final_time = state.get("last_final_time", 0.0)
                        now_m = time.monotonic()
                        since_commit = (now_m - last_final_time) if last_final_time > 0 else 999.0
                        no_new_audio = last_final_time > 0 and since_commit < 2.0
                        if no_new_audio:
                            for prior in state.get("recent_finals", []):
                                if not prior:
                                    continue
                                if normalized_new == prior:
                                    print(f"[dictation] buffer: dropped exact re-emission (gap={since_commit:.1f}s, no new hyp) {text!r}")
                                    return
                                if (
                                    prior.endswith(normalized_new)
                                    and len(normalized_new) < len(prior)
                                ):
                                    print(f"[dictation] buffer: dropped suffix re-emission (gap={since_commit:.1f}s, no new hyp) {text!r}")
                                    return
                        # Exact-dup of current pending: ignore without resetting
                        # the timer. Whisper keeps emitting the same final during
                        # trailing silence; we don't want those to delay commit.
                        if pending:
                            normalized_pending = " ".join(pending.lower().split())
                            if normalized_new == normalized_pending:
                                return
                            # Suffix-dup: new is already contained at the end of
                            # pending. Happens after a glue when whisper re-emits
                            # just the last fragment. Drop silently.
                            if normalized_pending.endswith(normalized_new) and len(normalized_new) < len(normalized_pending):
                                return
                        prev_timer = state.get("pending_final_timer")
                        if prev_timer is not None:
                            prev_timer.cancel()
                        if pending:
                            merged = (pending + " " + text).strip()
                            print(f"[dictation] pending glue: {pending!r} + {text!r} -> {merged!r}")
                            state["pending_final_text"] = merged
                        else:
                            state["pending_final_text"] = text
                            print(f"[dictation] pending new: {text!r}")
                        timer = threading.Timer(_PENDING_WINDOW_SECONDS, _flush_pending)
                        timer.daemon = True
                        timer.start()
                        state["pending_final_timer"] = timer

                state["_flush_pending"] = _flush_pending

                def _handle(event: LiveDictationEvent) -> None:
                    try:
                        name = event.event
                        text = (event.text or "").strip()
                        if name in ("ready", "error", "stopped"):
                            print(f"[dictation] stream event: {name} text={text!r}")
                            if name == "ready":
                                try:
                                    self.dictation_stream_ready.emit(
                                        self._dictation_backend_label()
                                    )
                                except Exception:
                                    pass
                            return
                        if name == "hypothesis":
                            if not text:
                                return
                            state["last_hypothesis_time"] = time.monotonic()
                            words = text.split()
                            stable = _common_prefix(state["last_words"], words)
                            state["last_words"] = words
                            commit_words = stable[:-hold_back] if len(stable) > hold_back else []
                            if not commit_words:
                                return
                            commit_text = " ".join(commit_words)
                            committed = state["committed"]
                            if commit_text.startswith(committed) and len(commit_text) > len(committed):
                                suffix = commit_text[len(committed):]
                                with self._corrector_lock:
                                    inserted = self.text_input_controller.insert_text(suffix)
                                    if inserted:
                                        self.grammar_corrector.append(suffix)
                                if inserted:
                                    state["committed"] = commit_text
                                    state["stream_hyp_chars"] = state.get("stream_hyp_chars", 0) + len(suffix)
                            return
                        if name in ("final", "rejected"):
                            if not text:
                                state["committed"] = ""
                                state["last_words"] = []
                                return
                            text = _strip_whisper_hallucinations(text)
                            if not text:
                                state["committed"] = ""
                                state["last_words"] = []
                                return
                            command = _parse_dictation_command(text)
                            if command is not None:
                                _flush_pending()
                                newline_text = "\n\n" if command == "paragraph" else "\n"
                                with self._corrector_lock:
                                    inserted = self.text_input_controller.insert_text(
                                        newline_text, prefer_paste=False
                                    )
                                    if inserted:
                                        self.grammar_corrector.append(newline_text)
                                print(f"[dictation] command: {command} inserted={inserted} text={text!r}")
                                state["committed"] = ""
                                state["last_words"] = []
                                state["last_final_text"] = ""
                                state["stream_hyp_chars"] = 0
                                return
                            _buffer_final(text)
                    except Exception:
                        # Never let a handler exception kill the stream
                        pass

                streamer = self.live_dictation_streamer
                # System.Speech occasionally ends its Multiple-mode session on
                # its own (timeouts, audio device hiccups). Restart it until
                # the user actually toggles dictation off.
                try:
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            break
                        # Reset per-utterance commit state so a fresh stream
                        # doesn't try to diff against stale words.
                        state["committed"] = ""
                        state["last_words"] = []
                        ok = streamer.stream(stop_event=stop_event, event_callback=_handle)
                        if stop_event is not None and stop_event.is_set():
                            break
                        if not ok:
                            final_message = streamer.message or final_message
                            break
                        # Stream returned cleanly without stop_event -- loop and restart.
                except Exception as exc:
                    final_message = f"dictation error: {exc}"

                if stop_event is None or not stop_event.is_set():
                    self._voice_queue.put(
                        (
                            request_id,
                            {
                                "event": "dictation_complete",
                                "success": bool(state["final_display"]),
                                "heard_text": "",
                                "control_text": final_message,
                                "display_text": state["final_display"],
                            },
                        )
                    )

            self._voice_thread = threading.Thread(target=_dictation_worker, name="hgr-app-dictation", daemon=True)
            self._voice_thread.start()
            return

        if mode == "save_prompt":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text=self._save_prompt_text,
            )
            self.command_detected.emit(self._save_prompt_text)
            self._emit_status("save prompt active")
        elif mode == "selection" and self._selection_prompt_active:
            self.voice_status_overlay.update_selection_status("Listening...")
        elif mode == "create_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="What would you like to call the playlist?",
            )
        elif mode == "add_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="Which playlist should I add to?",
            )
        elif mode == "remove_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="Which playlist should I remove from?",
            )
        else:
            self.voice_status_overlay.show_listening()
        if mode != "save_prompt":
            self._emit_status("voice listening...")

        chrome_mode_voice = self._chrome_mode_enabled and self.chrome_controller.is_window_open()
        preferred_target = "chrome" if chrome_mode_voice and mode == "general" else preferred_app

        def _push_status(status: str, *, command_text: str = "") -> None:
            self._voice_queue.put((request_id, {"event": "status", "status": status, "command_text": command_text}))

        def _worker() -> None:
            payload: dict | None = None
            heard_text = ""
            try:
                if mode == "save_prompt":
                    transcript_mode = "save_prompt"
                    listen_seconds = 9.5
                else:
                    transcript_mode = "playlist" if mode in {"add_playlist", "remove_playlist", "create_playlist"} else "command"
                    # Budget = start-wait (5s) + utterance headroom (~4s)
                    # + end-silence (3s) for commands. The old 5.0s was
                    # a total cap too tight for any of those — the loop
                    # ran out of blocks before the silence check could
                    # fire, so recordings cut after ~2s of voice audio.
                    listen_seconds = 8.5 if transcript_mode == "playlist" else 12.0
                result = self.voice_listener.listen(
                    max_seconds=listen_seconds,
                    status_callback=_push_status,
                    transcript_mode=transcript_mode,
                )
                heard_text = result.heard_text
                if mode in {"general", "selection"} and result.success:
                    _push_status("processing", command_text=result.heard_text)
                    context = VoiceCommandContext(preferred_app=preferred_target) if mode == "general" else None
                    try:
                        execution = self.voice_processor.execute(result.heard_text, context=context)
                    except Exception as exc:
                        traceback.print_exc()
                        payload = {
                            "event": "result",
                            "mode": mode,
                            "success": False,
                            "target": "voice",
                            "heard_text": result.heard_text,
                            "control_text": f"voice command failed: {type(exc).__name__}",
                            "info_text": (str(exc) or "see console")[:200],
                            "display_text": result.heard_text,
                        }
                    else:
                        payload = {
                            "event": "result",
                            "mode": mode,
                            "success": execution.success,
                            "target": execution.target,
                            "heard_text": execution.heard_text,
                            "control_text": execution.control_text,
                            "info_text": execution.info_text,
                            "display_text": getattr(execution, "display_text", execution.heard_text),
                        }
                else:
                    payload = {
                        "event": "result",
                        "mode": mode,
                        "success": result.success,
                        "target": "save_prompt" if mode == "save_prompt" else "voice",
                        "heard_text": result.heard_text,
                        "control_text": result.message,
                        "info_text": self._spotify_info_text,
                        "display_text": result.heard_text,
                    }
            except Exception as exc:
                traceback.print_exc()
                payload = {
                    "event": "result",
                    "mode": mode,
                    "success": False,
                    "target": "voice",
                    "heard_text": heard_text,
                    "control_text": f"voice worker crashed: {type(exc).__name__}",
                    "info_text": (str(exc) or "see console")[:200],
                    "display_text": heard_text or "voice error",
                }
            finally:
                if payload is None:
                    payload = {
                        "event": "result",
                        "mode": mode,
                        "success": False,
                        "target": "voice",
                        "heard_text": heard_text,
                        "control_text": "voice worker exited without result",
                        "info_text": "-",
                        "display_text": heard_text or "voice error",
                    }
                self._voice_queue.put((request_id, payload))

        self._voice_thread = threading.Thread(target=_worker, name="hgr-app-voice-command", daemon=True)
        self._voice_thread.start()

    def _drain_voice_results(self) -> None:
        while True:
            try:
                request_id, payload = self._voice_queue.get_nowait()
            except queue.Empty:
                break
            if request_id != self._voice_request_id:
                continue
            event = str(payload.get("event", "result"))
            if event == "status":
                status = str(payload.get("status", "") or "").strip().lower()
                command_text = str(payload.get("command_text", "") or "").strip()
                if command_text:
                    self._voice_heard_text = command_text
                    self._voice_display_text = command_text
                if status == "listening":
                    if self._voice_mode == "dictation":
                        self._voice_control_text = "dictation listening..."
                    elif self._voice_mode == "save_prompt":
                        self._voice_control_text = self._save_prompt_text
                    else:
                        self._voice_control_text = "voice listening..."
                    if self._voice_mode == "dictation":
                        self.voice_status_overlay.show_processing("Dictation active", command_text="")
                    elif self._voice_mode == "save_prompt":
                        self.voice_status_overlay.show_listening("Listening...", hint_text=self._save_prompt_text)
                    else:
                        if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                            self.voice_status_overlay.update_selection_status("Listening...")
                        else:
                            self.voice_status_overlay.show_listening()
                elif status == "recognizing":
                    if self._voice_mode == "dictation":
                        self._voice_control_text = "dictation active"
                        self.voice_status_overlay.show_processing("Dictation active", command_text="")
                    elif self._voice_mode == "save_prompt":
                        self._voice_control_text = "recognizing save location..."
                        self.voice_status_overlay.show_processing(
                            "Processing command...",
                            command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                        )
                    else:
                        self._voice_control_text = "recognizing..."
                        if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                            self.voice_status_overlay.update_selection_status("Recognizing...")
                        else:
                            self.voice_status_overlay.show_processing(
                                "Recognizing...",
                                command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                            )
                elif status == "processing":
                    if self._voice_mode == "save_prompt":
                        self._voice_control_text = "processing save location..."
                    else:
                        self._voice_control_text = "processing command..."
                    if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                        self.voice_status_overlay.hide_overlay()
                    elif self._voice_mode == "save_prompt":
                        self.voice_status_overlay.show_processing(
                            "Executing command...",
                            command_text=command_text or (self._voice_display_text if self._voice_display_text != "-" else ""),
                        )
                    else:
                        self.voice_status_overlay.show_processing(
                            "Processing command...",
                            command_text=command_text or (self._voice_display_text if self._voice_display_text != "-" else ""),
                        )
                continue
            if event == "dictation_chunk":
                heard_text = str(payload.get("heard_text", "") or "").strip()
                display_text = str(payload.get("display_text", "") or "").strip()
                partial = bool(payload.get("partial"))
                if heard_text:
                    self._voice_heard_text = heard_text
                if display_text:
                    self._voice_display_text = display_text
                self._voice_control_text = str(payload.get("control_text", "dictation updated"))
                if partial:
                    self._voice_control_text = "live dictating..."
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_listening(backend_label=self._dictation_backend_label())
                continue
            if event == "dictation_complete":
                self._voice_listening = False
                self._dictation_active = False
                self._dictation_backend = "idle"
                self._voice_mode = "ready"
                self._voice_stop_event = None
                try:
                    self.grammar_corrector.stop()
                except Exception:
                    pass
                try:
                    if self.whisper_refiner is not None:
                        self.whisper_refiner.stop()
                except Exception:
                    pass
                self._dictation_state = None
                display_text = str(payload.get("display_text", "") or "").strip()
                if display_text:
                    self._voice_display_text = display_text
                self._voice_control_text = str(payload.get("control_text", "dictation stopped"))
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Dictation stopped",
                    command_text="",
                    duration=1.8,
                )
                continue
            self._voice_listening = False
            self._dictation_active = False
            self._dictation_backend = "idle"
            self._voice_mode = "ready"
            self._voice_stop_event = None
            try:
                self.grammar_corrector.stop()
            except Exception:
                pass
            try:
                if self.whisper_refiner is not None:
                    self.whisper_refiner.stop()
            except Exception:
                pass
            self._dictation_state = None
            mode = str(payload.get("mode", "general"))
            heard_text = str(payload.get("heard_text", "") or "").strip()
            self._voice_heard_text = heard_text or "-"
            self._voice_display_text = str(payload.get("display_text", "") or heard_text or "-")
            self._voice_control_text = str(payload.get("control_text", "voice idle"))
            target = payload.get("target")
            if mode == "general":
                if target == "spotify":
                    if payload.get("success"):
                        self._spotify_info_text = str(payload.get("info_text", self._spotify_info_text))
                    self._spotify_control_text = str(payload.get("control_text", self._spotify_control_text))
                elif target == "chrome":
                    self._chrome_control_text = str(payload.get("control_text", self._chrome_control_text))
                if target == "voice_selection":
                    self.command_detected.emit(self._voice_control_text)
                    title, items, instruction = self._parse_voice_selection_text(self._voice_display_text if self._voice_display_text != "-" else "")
                    self._selection_prompt_active = True
                    self._selection_prompt_title = title
                    self._selection_prompt_items = items
                    self._selection_prompt_instruction = instruction
                    self.voice_status_overlay.show_selection(title, items, instruction, status_text="")
                    continue
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Executing command" if payload.get("success") else "Command not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "selection":
                if target == "voice_selection":
                    self.command_detected.emit(self._voice_control_text)
                    title, items, instruction = self._parse_voice_selection_text(self._voice_display_text if self._voice_display_text != "-" else "")
                    self._selection_prompt_active = True
                    self._selection_prompt_title = title
                    self._selection_prompt_items = items
                    self._selection_prompt_instruction = instruction
                    self.voice_status_overlay.show_selection(title, items, instruction, status_text="")
                    continue
                self._selection_prompt_active = False
                if str(self._voice_control_text).lower().startswith("selection cancelled"):
                    self.command_detected.emit(self._voice_control_text)
                    self.voice_status_overlay.show_result("Selection cancelled", command_text="", duration=1.4)
                    continue
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Executing command" if payload.get("success") else "Command not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "save_prompt":
                self._save_prompt_active = False
                self.command_detected.emit("save destination received" if heard_text else "using default save location")
                self.voice_status_overlay.show_result(
                    "Save preference received" if heard_text else "Using default save folder",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=1.5,
                )
                self.save_prompt_completed.emit(
                    {
                        "success": bool(payload.get("success")),
                        "heard_text": heard_text,
                        "control_text": self._voice_control_text,
                        "display_text": self._voice_display_text,
                    }
                )
                continue

            if not payload.get("success"):
                self._spotify_control_text = str(payload.get("control_text", "voice command not understood"))
                self.command_detected.emit(self._spotify_control_text)
                self.voice_status_overlay.show_result(
                    "Playlist not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "create_playlist":
                playlist_name = self._clean_create_playlist_reply(heard_text)
            else:
                playlist_name = self._clean_playlist_reply(heard_text)
            if not playlist_name:
                self._spotify_control_text = "playlist name not understood"
                self.command_detected.emit(self._spotify_control_text)
                self.voice_status_overlay.show_result(
                    "Playlist not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "add_playlist":
                success = self.spotify_controller.add_current_track_to_playlist(playlist_name)
            elif mode == "create_playlist":
                success = self.spotify_controller.create_playlist(playlist_name)
            else:
                success = self.spotify_controller.remove_current_track_from_playlist(playlist_name)
            self._spotify_control_text = self.spotify_controller.message
            self.command_detected.emit(self._spotify_control_text)
            if success:
                details = self.spotify_controller.get_current_track_details()
                if details is not None:
                    self._spotify_info_text = details.summary()
            self.voice_status_overlay.show_result(
                self.spotify_controller.message,
                command_text=playlist_name,
                duration=2.0,
            )

    def _parse_voice_selection_text(self, text: str) -> tuple[str, list[tuple[int, str, str]], str]:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        title = "Choose a file or folder"
        if lines and "apps" in lines[0].lower():
            title = "Choose a file or app"
        instruction = "Say the corresponding number"
        items: list[tuple[int, str, str]] = []
        for line in lines:
            match = re.match(r"^(\d+)\.\s+(.*?)(?:\s+[—-]\s+(.*))?$", line)
            if match:
                number = int(match.group(1))
                label = str(match.group(2) or "").strip()
                path_text = str(match.group(3) or "").strip()
                items.append((number, label, path_text))
            elif line.lower().startswith("which "):
                instruction = line
        return title, items, instruction

    def _on_dictation_stream_ready(self, backend_label: str) -> None:
        try:
            self.voice_status_overlay.show_listening(backend_label=backend_label)
        except Exception:
            pass

    def _handle_voice_overlay_selection(self, number: int) -> None:
        try:
            result = self.voice_processor.execute(str(number))
        except Exception:
            return
        self._voice_queue.put((self._voice_request_id, {
            "event": "result",
            "mode": "general",
            "success": result.success,
            "target": result.target,
            "heard_text": result.heard_text,
            "control_text": result.control_text,
            "info_text": result.info_text,
            "display_text": getattr(result, "display_text", result.heard_text),
        }))

    def _update_spotify_wheel_overlay(self, now: float) -> None:
        if not self._spotify_wheel_visible:
            if self.spotify_wheel_overlay.isVisible():
                self.spotify_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._spotify_wheel_selected_key is not None:
            selected_label = self._spotify_wheel_label(self._spotify_wheel_selected_key)
        selection_progress = 0.0
        if self._spotify_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._spotify_wheel_selected_since) / 1.0))
        self.spotify_wheel_overlay.set_wheel(
            items=self._spotify_wheel_items(),
            selected_key=self._spotify_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._spotify_wheel_cursor_offset,
        )
        self.spotify_wheel_overlay.show_overlay()

    def _update_chrome_wheel_overlay(self, now: float) -> None:
        if not self._chrome_wheel_visible:
            if self.chrome_wheel_overlay.isVisible():
                self.chrome_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._chrome_wheel_selected_key is not None:
            selected_label = self._chrome_wheel_label(self._chrome_wheel_selected_key)
        selection_progress = 0.0
        if self._chrome_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._chrome_wheel_selected_since) / 1.0))
        self.chrome_wheel_overlay.set_wheel(
            items=self._chrome_wheel_items(),
            selected_key=self._chrome_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._chrome_wheel_cursor_offset,
        )
        self.chrome_wheel_overlay.show_overlay()

    def _update_youtube_wheel_overlay(self, now: float) -> None:
        if not self._youtube_wheel_visible:
            if self.youtube_wheel_overlay.isVisible():
                self.youtube_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._youtube_wheel_selected_key is not None:
            selected_label = self._youtube_wheel_label(self._youtube_wheel_selected_key)
        selection_progress = 0.0
        if self._youtube_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._youtube_wheel_selected_since) / 1.0))
        self.youtube_wheel_overlay.set_wheel(
            items=self._youtube_wheel_items(),
            selected_key=self._youtube_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._youtube_wheel_cursor_offset,
        )
        self.youtube_wheel_overlay.show_overlay()

    def _update_drawing_wheel_overlay(self, now: float) -> None:
        if not self._drawing_wheel_visible:
            if self.drawing_wheel_overlay.isVisible():
                self.drawing_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._drawing_wheel_selected_key is not None:
            selected_label = self._drawing_wheel_label(self._drawing_wheel_selected_key)
        selection_progress = 0.0
        if self._drawing_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._drawing_wheel_selected_since) / 0.85))
        self.drawing_wheel_overlay.set_wheel(
            items=self._drawing_wheel_items(),
            selected_key=self._drawing_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._drawing_wheel_cursor_offset,
        )
        self.drawing_wheel_overlay.show_overlay()

    def _update_utility_wheel_overlay(self, now: float) -> None:
        if not self._utility_wheel_visible:
            if self.utility_wheel_overlay.isVisible():
                self.utility_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._utility_wheel_selected_key is not None:
            selected_label = self._utility_wheel_label(self._utility_wheel_selected_key)
        selection_progress = 0.0
        if self._utility_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._utility_wheel_selected_since) / 0.85))
        self.utility_wheel_overlay.set_wheel(
            items=self._utility_wheel_items(),
            selected_key=self._utility_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._utility_wheel_cursor_offset,
        )
        self.utility_wheel_overlay.show_overlay()

    def _cancel_all_voice_stages(self) -> None:
        was_active = bool(
            self._voice_listening
            or self._dictation_active
            or self._save_prompt_active
            or self._selection_prompt_active
        )
        was_save_prompt = bool(self._save_prompt_active)
        self._reset_voice_state()
        if was_save_prompt:
            try:
                self.save_prompt_completed.emit(
                    {
                        "success": False,
                        "heard_text": "",
                        "control_text": "save prompt canceled",
                        "display_text": "-",
                        "canceled": True,
                    }
                )
            except Exception:
                pass
        if was_active:
            self._voice_control_text = "voice canceled"
            try:
                self.command_detected.emit(self._voice_control_text)
            except Exception:
                pass
            try:
                self.voice_status_overlay.show_result("Canceled", command_text="", duration=1.0)
            except Exception:
                pass

    def _reset_voice_state(self) -> None:
        if self._voice_stop_event is not None:
            self._voice_stop_event.set()
            self._voice_stop_event = None
        self.dictation_processor.reset()
        try:
            self.grammar_corrector.stop()
        except Exception:
            pass
        try:
            if self.whisper_refiner is not None:
                self.whisper_refiner.stop()
        except Exception:
            pass
        self._dictation_state = None
        self._voice_candidate = "neutral"
        self._voice_candidate_since = 0.0
        self._voice_cooldown_until = 0.0
        self._voice_latched_label = None
        self._voice_request_id += 1
        self._voice_listening = False
        self._dictation_active = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_control_text = self.voice_listener.message
        self._voice_heard_text = "-"
        self._voice_display_text = "-"
        self._dictation_toggle_release_required = False
        self._dictation_stop_rearm_at = 0.0
        self._dictation_release_candidate_since = 0.0
        self._selection_prompt_active = False
        self._selection_prompt_title = "Which file/folder?"
        self._selection_prompt_items = []
        self._selection_prompt_instruction = "Say the corresponding number."
        self._save_prompt_active = False
        self.voice_status_overlay.hide_overlay()
        while True:
            try:
                self._voice_queue.get_nowait()
            except queue.Empty:
                break

    def start_save_location_prompt(self) -> bool:
        if not self._running:
            return False
        if self._voice_listening:
            self._reset_voice_state()
        self._save_prompt_active = True
        self._start_voice_capture(mode="save_prompt", preferred_app=None)
        return True
