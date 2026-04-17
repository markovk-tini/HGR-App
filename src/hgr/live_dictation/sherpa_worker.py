"""Streaming ASR worker powered by sherpa-onnx.

Runs in its own daemon thread. Captures 16 kHz mono audio via
``sounddevice``, feeds it to a streaming zipformer transducer, and
fires callbacks for each partial hypothesis and endpoint.

The worker is intentionally decoupled from the rest of the app: it
speaks only via the callbacks it was given, so the controller can
swap this backend out for another one without cascading changes.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections.abc import Callable

from . import config

log = logging.getLogger(__name__)


SAMPLE_RATE = 16_000
BLOCKSIZE = 1600  # 100 ms at 16 kHz


class SherpaWorker:
    def __init__(
        self,
        on_partial: Callable[[str], None],
        on_endpoint: Callable[[str], None],
        on_state: Callable[[str, str | None], None],
        device: int | None = None,
    ) -> None:
        self._on_partial = on_partial
        self._on_endpoint = on_endpoint
        self._on_state = on_state
        self._device = device
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._audio_q: "queue.Queue[object]" = queue.Queue(maxsize=64)

    @staticmethod
    def available() -> tuple[bool, str | None]:
        return config.sherpa_backend_available()

    def start(self) -> bool:
        ok, err = self.available()
        if not ok:
            self._on_state("error", err)
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="hgr-sherpa-asr", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.5)
        self._thread = None

    def _build_recognizer(self):
        import glob

        import sherpa_onnx

        md = config.resolve_sherpa_model_dir()
        assert md is not None

        def _find(name: str) -> str:
            direct = os.path.join(md, name)
            if os.path.isfile(direct):
                return direct
            stem = os.path.splitext(name)[0]
            matches = sorted(glob.glob(os.path.join(md, f"{stem}*")))
            if matches:
                return matches[0]
            raise FileNotFoundError(f"{name} (or {stem}*) not found in {md}")

        encoder = _find("encoder.onnx")
        decoder = _find("decoder.onnx")
        joiner = _find("joiner.onnx")
        tokens = _find("tokens.txt")
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=tokens,
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            provider="cpu",
            num_threads=2,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.0,
            rule3_min_utterance_length=300,
            decoding_method="greedy_search",
        )

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("sounddevice status: %s", status)
        try:
            self._audio_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            log.warning("audio queue full; dropping chunk")

    def _run(self) -> None:
        import sounddevice as sd

        try:
            recognizer = self._build_recognizer()
            stream = recognizer.create_stream()
        except Exception as exc:
            log.exception("sherpa recognizer build failed")
            self._on_state("error", f"recognizer init failed: {exc}")
            return

        self._on_state("listening", None)

        try:
            in_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCKSIZE,
                device=self._device,
                callback=self._audio_callback,
            )
        except Exception as exc:
            log.exception("microphone open failed")
            self._on_state("error", f"mic open failed: {exc}")
            return

        last_partial = ""
        first_audio_ts: float | None = None
        first_partial_logged = False

        try:
            in_stream.start()
            while not self._stop_event.is_set():
                try:
                    chunk = self._audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if first_audio_ts is None:
                    first_audio_ts = time.monotonic()
                stream.accept_waveform(SAMPLE_RATE, chunk)
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
                text = recognizer.get_result(stream).strip()
                if text != last_partial:
                    if text and not first_partial_logged:
                        log.info(
                            "first-partial latency: %.3fs",
                            time.monotonic() - first_audio_ts,
                        )
                        first_partial_logged = True
                        self._on_state("speaking", None)
                    last_partial = text
                    if text:
                        self._on_partial(text)
                if recognizer.is_endpoint(stream):
                    final = text
                    log.debug("endpoint: %r", final)
                    try:
                        self._on_endpoint(final)
                    except Exception:
                        log.exception("endpoint callback failed")
                    recognizer.reset(stream)
                    last_partial = ""
                    first_partial_logged = False
                    self._on_state("listening", None)
        except Exception as exc:
            log.exception("sherpa worker crashed")
            self._on_state("error", f"asr worker: {exc}")
        finally:
            try:
                in_stream.stop()
                in_stream.close()
            except Exception:
                pass
            self._on_state("off", None)
