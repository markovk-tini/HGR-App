from __future__ import annotations

import os
import re
import time
import wave
import tempfile
import threading
import subprocess
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import keyboard
import numpy as np
import sounddevice as sd
from hgr.utils.runtime_paths import app_base_path, resource_path


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

BLOCK_MS = 100
BLOCK_FRAMES = SAMPLE_RATE * BLOCK_MS // 1000
PRE_ROLL_MS = 400
PRE_ROLL_BLOCKS = max(1, PRE_ROLL_MS // BLOCK_MS)

START_RMS = 180.0
END_RMS = 90.0
END_SILENCE_SEC = 1.7
MIN_UTTERANCE_SEC = 0.55

PARTIAL_INTERVAL_SEC = 0.55
PARTIAL_MIN_AUDIO_SEC = 1.0
LIVE_HOLD_BACK_WORDS = 2
APPEND_SPACE_AFTER_FINAL = True
LANGUAGE = "en"
THREADS = str(max(1, min(8, (os.cpu_count() or 4))))

LIVE_MODEL_NAMES = [
    "ggml-small.en-q5_0.bin",
    "ggml-small.en-q8_0.bin",
    "ggml-small.en.bin",
    "ggml-base.en-q5_0.bin",
    "ggml-base.en-q8_0.bin",
    "ggml-base.en.bin",
    "ggml-medium.en.bin",
]

FINAL_MODEL_NAMES = [
    "ggml-medium.en.bin",
    "ggml-small.en-q8_0.bin",
    "ggml-small.en.bin",
    "ggml-base.en-q8_0.bin",
    "ggml-base.en.bin",
]

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
BRACKET_TAG_RE = re.compile(r"\[[A-Z_]+\]")
MULTISPACE_RE = re.compile(r"\s+")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
LOWER_I_RE = re.compile(r"(?<![A-Za-z])i(?![A-Za-z])")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
IGNORE_LINE_PREFIXES = ("system_info:", "main:", "whisper_")


def _project_root() -> Path:
    bundle_root = app_base_path()
    bundled_whisper = resource_path("whisper.cpp")
    if bundled_whisper.exists():
        return bundle_root
    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "whisper.cpp").exists():
            return parent
    return current.parent


PROJECT_ROOT = _project_root()
WHISPER_ROOT = resource_path("whisper.cpp") if resource_path("whisper.cpp").exists() else (PROJECT_ROOT / "whisper.cpp")
CLI_CANDIDATES = [
    Path(os.getenv("HGR_WHISPER_CPP", "")).expanduser() if os.getenv("HGR_WHISPER_CPP") else None,
    WHISPER_ROOT / "build" / "bin" / "Release" / "whisper-cli.exe",
    WHISPER_ROOT / "build" / "bin" / "whisper-cli.exe",
    WHISPER_ROOT / "build_stream" / "bin" / "Release" / "whisper-cli.exe",
    WHISPER_ROOT / "build_stream" / "bin" / "whisper-cli.exe",
    PROJECT_ROOT / "whisper-cli.exe",
]


def iter_files(root: Path):
    skip_dirs = {
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".idea",
        ".vscode",
        "node_modules",
    }
    if not root.exists():
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            for entry in current.iterdir():
                if entry.is_dir():
                    if entry.name.lower() in skip_dirs:
                        continue
                    stack.append(entry)
                else:
                    yield entry
        except Exception:
            continue


def find_whisper_cli() -> Optional[Path]:
    for candidate in CLI_CANDIDATES:
        if candidate is not None and candidate.exists():
            return candidate
    if WHISPER_ROOT.exists():
        for file in iter_files(WHISPER_ROOT) or []:
            if file.name.lower() == "whisper-cli.exe":
                return file
    return None


def find_model_by_names(names: list[str], *, env_key: str | None = None) -> Optional[Path]:
    if env_key:
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            path = Path(env_value).expanduser()
            if path.exists():
                return path

    search_roots = [
        WHISPER_ROOT / "models",
        WHISPER_ROOT,
        PROJECT_ROOT,
    ]
    for name in names:
        for root in search_roots:
            candidate = root / name
            if candidate.exists():
                return candidate

    wanted = {name.lower() for name in names}
    for file in iter_files(PROJECT_ROOT) or []:
        if file.name.lower() in wanted:
            return file
    return None


def rms_int16(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    array = audio.astype(np.float32)
    return float(np.sqrt(np.mean(array * array)))


def write_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio.astype(np.int16).tobytes())


def strip_cli_noise(text: str) -> str:
    text = ANSI_RE.sub("", text or "")
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(prefix) for prefix in IGNORE_LINE_PREFIXES):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def normalize_partial_text(text: str) -> str:
    text = strip_cli_noise(text)
    text = BRACKET_TAG_RE.sub("", text)
    words = WORD_RE.findall(text)
    return " ".join(words).strip()


def cleanup_final_text(text: str) -> str:
    text = strip_cli_noise(text)
    text = BRACKET_TAG_RE.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = MULTISPACE_RE.sub(" ", text).strip()
    text = SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = LOWER_I_RE.sub("I", text)
    if text:
        text = text[0].upper() + text[1:]
    text = re.sub(r"([.!?]\s+)([a-z])", lambda match: match.group(1) + match.group(2).upper(), text)
    if text and text[-1] not in ".!?":
        text += "."
    return text


def common_prefix_words(a: str, b: str) -> int:
    words_a = a.split()
    words_b = b.split()
    limit = min(len(words_a), len(words_b))
    index = 0
    while index < limit and words_a[index].lower() == words_b[index].lower():
        index += 1
    return index


def first_n_words(text: str, n: int) -> str:
    return " ".join(text.split()[:n])


def stable_commit_text(partial_text: str, hold_back_words: int) -> str:
    words = partial_text.split()
    if len(words) <= hold_back_words:
        return ""
    return " ".join(words[:-hold_back_words])


def backspace_n(n: int) -> None:
    for _ in range(max(0, n)):
        keyboard.send("backspace")


class GestureDictationSession:
    def __init__(
        self,
        *,
        on_update: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_update = on_update or (lambda payload: None)
        self._on_error = on_error or (lambda message: None)
        self.active = False
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.type_lock = threading.Lock()
        self.decode_event = threading.Event()
        self.pending_partial: Optional[tuple[int, np.ndarray]] = None
        self.pending_final: Optional[tuple[int, np.ndarray]] = None
        self.current_utt_id = 0
        self.current_live_typed = ""
        self.prev_partial_text = ""
        self._capture_thread: threading.Thread | None = None
        self._decode_thread: threading.Thread | None = None
        self.cli: Path | None = None
        self.live_model: Path | None = None
        self.final_model: Path | None = None
        self._refresh_paths()

    def _refresh_paths(self) -> None:
        self.cli = find_whisper_cli()
        self.live_model = find_model_by_names(LIVE_MODEL_NAMES, env_key="HGR_WHISPER_CPP_MODEL")
        self.final_model = find_model_by_names(FINAL_MODEL_NAMES, env_key="HGR_WHISPER_CPP_MODEL")

    def ready(self) -> tuple[bool, str]:
        self._refresh_paths()
        if self.cli is None:
            return False, "whisper-cli.exe not found"
        if self.live_model is None:
            return False, "no live dictation model found"
        if self.final_model is None:
            return False, "no final dictation model found"
        return True, "dictation ready"

    def start(self) -> tuple[bool, str]:
        if self.active:
            return True, "dictation active"
        ok, message = self.ready()
        if not ok:
            return False, message

        self.stop_event.clear()
        self.decode_event.clear()
        self.pending_partial = None
        self.pending_final = None
        self.current_utt_id = 0
        self.current_live_typed = ""
        self.prev_partial_text = ""
        self.active = True

        self._capture_thread = threading.Thread(target=self._capture_loop, name="hgr-dictation-capture", daemon=True)
        self._decode_thread = threading.Thread(target=self._decode_loop, name="hgr-dictation-decode", daemon=True)
        self._capture_thread.start()
        self._decode_thread.start()
        return True, "dictation active"

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self.stop_event.set()
        self.decode_event.set()

    def _capture_loop(self) -> None:
        block_sec = BLOCK_FRAMES / SAMPLE_RATE
        pre_roll: deque[np.ndarray] = deque(maxlen=PRE_ROLL_BLOCKS)
        in_speech = False
        utterance_chunks: list[np.ndarray] = []
        silence_sec = 0.0
        last_partial_request = 0.0
        utt_id = 0
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_FRAMES,
            ) as stream:
                while not self.stop_event.is_set():
                    data, overflowed = stream.read(BLOCK_FRAMES)
                    chunk = np.asarray(data).reshape(-1).astype(np.int16)
                    if overflowed:
                        continue
                    level = rms_int16(chunk)
                    if not in_speech:
                        pre_roll.append(chunk)
                        if level >= START_RMS:
                            in_speech = True
                            silence_sec = 0.0
                            last_partial_request = 0.0
                            utterance_chunks = list(pre_roll)
                            utterance_chunks.append(chunk)
                            with self.state_lock:
                                self.current_utt_id += 1
                                utt_id = self.current_utt_id
                                self.current_live_typed = ""
                                self.prev_partial_text = ""
                    else:
                        utterance_chunks.append(chunk)
                        if level >= END_RMS:
                            silence_sec = 0.0
                        else:
                            silence_sec += block_sec

                        utterance_audio = np.concatenate(utterance_chunks)
                        utterance_seconds = len(utterance_audio) / SAMPLE_RATE
                        if (
                            utterance_seconds >= PARTIAL_MIN_AUDIO_SEC
                            and (time.time() - last_partial_request) >= PARTIAL_INTERVAL_SEC
                        ):
                            self._set_pending_partial(utt_id, utterance_audio.copy())
                            last_partial_request = time.time()

                        if silence_sec >= END_SILENCE_SEC:
                            final_audio = utterance_audio.copy()
                            final_seconds = len(final_audio) / SAMPLE_RATE
                            if final_seconds >= MIN_UTTERANCE_SEC:
                                self._set_pending_final(utt_id, final_audio)
                            in_speech = False
                            utterance_chunks = []
                            pre_roll.clear()
                            silence_sec = 0.0
                            last_partial_request = 0.0
        except Exception as exc:
            self._on_error(f"dictation capture failed: {type(exc).__name__}")
        finally:
            self.stop()

    def _set_pending_partial(self, utt_id: int, audio: np.ndarray) -> None:
        with self.state_lock:
            self.pending_partial = (utt_id, audio)
            self.decode_event.set()

    def _set_pending_final(self, utt_id: int, audio: np.ndarray) -> None:
        with self.state_lock:
            self.pending_final = (utt_id, audio)
            self.pending_partial = None
            self.decode_event.set()

    def _decode_loop(self) -> None:
        while not self.stop_event.is_set():
            self.decode_event.wait(timeout=0.2)
            if self.stop_event.is_set():
                break
            task = None
            with self.state_lock:
                if self.pending_final is not None:
                    utt_id, audio = self.pending_final
                    self.pending_final = None
                    task = ("final", utt_id, audio, self.final_model)
                elif self.pending_partial is not None:
                    utt_id, audio = self.pending_partial
                    self.pending_partial = None
                    task = ("partial", utt_id, audio, self.live_model)
                if self.pending_final is None and self.pending_partial is None:
                    self.decode_event.clear()
            if task is None:
                continue
            kind, utt_id, audio, model = task
            try:
                raw = self._transcribe(audio, model)
                if kind == "partial":
                    self._apply_partial_result(utt_id, raw)
                else:
                    self._apply_final_result(utt_id, raw)
            except Exception as exc:
                self._on_error(f"dictation decode failed: {type(exc).__name__}: {exc}")
        self.stop()

    def _transcribe(self, audio: np.ndarray, model: Path | None) -> str:
        if self.cli is None or model is None:
            raise RuntimeError("dictation models unavailable")
        with tempfile.TemporaryDirectory(prefix="hgr_dict_") as temp_dir:
            wav_path = Path(temp_dir) / "utt.wav"
            write_wav(wav_path, audio)
            command = [
                str(self.cli),
                "-m",
                str(model),
                "-f",
                str(wav_path),
                "-l",
                LANGUAGE,
                "-t",
                THREADS,
                "-nt",
                "-np",
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "").strip())
            return result.stdout or ""

    def _apply_partial_result(self, utt_id: int, raw: str) -> None:
        partial_text = normalize_partial_text(raw)
        if not partial_text:
            return
        with self.state_lock:
            if utt_id != self.current_utt_id or self.stop_event.is_set():
                return
            previous_partial = self.prev_partial_text
            live_typed = self.current_live_typed
            stable_prefix_count = common_prefix_words(previous_partial, partial_text)
            stable_prefix_text = first_n_words(partial_text, stable_prefix_count)
            desired_live = stable_commit_text(stable_prefix_text, LIVE_HOLD_BACK_WORDS)
            self.prev_partial_text = partial_text
            if not desired_live or desired_live == live_typed or not desired_live.startswith(live_typed):
                return
            suffix = desired_live[len(live_typed):]
        if suffix:
            with self.type_lock:
                keyboard.write(suffix, delay=0)
            with self.state_lock:
                if utt_id == self.current_utt_id:
                    self.current_live_typed = desired_live
            self._on_update(
                {
                    "heard_text": partial_text,
                    "display_text": desired_live,
                    "control_text": "live dictating...",
                    "partial": True,
                }
            )

    def _apply_final_result(self, utt_id: int, raw: str) -> None:
        final_text = cleanup_final_text(raw)
        with self.state_lock:
            if utt_id != self.current_utt_id:
                return
            live_typed = self.current_live_typed
            self.current_live_typed = ""
            self.prev_partial_text = ""
        if not final_text:
            final_text = live_typed.strip()
        if not final_text:
            return
        replacement = final_text + (" " if APPEND_SPACE_AFTER_FINAL else "")
        with self.type_lock:
            if live_typed:
                backspace_n(len(live_typed))
            keyboard.write(replacement, delay=0)
        self._on_update(
            {
                "heard_text": final_text,
                "display_text": final_text,
                "control_text": "live dictation committed",
                "partial": False,
            }
        )
