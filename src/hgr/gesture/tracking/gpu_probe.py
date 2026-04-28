"""Detect what GPU-accelerated inference paths are reachable on this
machine, without raising or printing on import.

The gesture pipeline can in principle accelerate on the GPU through
two distinct stacks, with very different reliability characteristics
on Windows:

  1. MediaPipe Tasks API HandLandmarker with `BaseOptions.delegate =
     Delegate.GPU`. Same models as our existing `solutions.hands`
     code path, so accuracy is identical when it works. On Windows
     this delegate goes through OpenGL ES / Vulkan and frequently
     falls back to CPU silently — but when it does engage, the
     speedup is 2-3x with zero accuracy risk.

  2. ONNX Runtime with the DirectML execution provider on a
     custom palm-detect + landmark pipeline. Reliable but requires
     hand-rolled preprocessing, anchor decoding, NMS and ROI
     rotation matching MediaPipe's internals. Multi-day effort and
     an extra ~80 MB of provider DLLs in the installer.

This module just answers "is each path importable / instantiable
right now". Actual inference lives elsewhere — the `runtime.py`
loader uses these answers to pick a path or fall back to CPU
MediaPipe. The Settings toggle uses them to decide whether the
GPU Mode checkbox should be enabled or shown disabled with a
tooltip explaining the user has no GPU path available.

The probe NEVER imports `onnxruntime-directml` or constructs a
HandLandmarker eagerly at app startup — too slow + side-effecty.
It only checks "could I import these modules cheaply" + reads
provider lists. Real construction happens lazily when the user
flips GPU Mode on.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class GpuProbeResult:
    """Snapshot of which GPU-accelerated inference paths are
    plausibly reachable. `path_summary()` formats it for the
    Settings tooltip when no path is available."""

    mediapipe_tasks_importable: bool
    tasks_gpu_delegate_present: bool   # the enum exists, not necessarily that it works
    onnxruntime_importable: bool
    onnxruntime_directml_provider: bool  # true iff "DmlExecutionProvider" is in get_available_providers()
    error_notes: tuple[str, ...]

    @property
    def has_any_gpu_path(self) -> bool:
        return (
            (self.mediapipe_tasks_importable and self.tasks_gpu_delegate_present)
            or (self.onnxruntime_importable and self.onnxruntime_directml_provider)
        )

    def path_summary(self) -> str:
        """Human-readable one-liner for the Settings tooltip."""
        parts: list[str] = []
        if self.mediapipe_tasks_importable and self.tasks_gpu_delegate_present:
            parts.append("MediaPipe Tasks GPU delegate")
        if self.onnxruntime_importable and self.onnxruntime_directml_provider:
            parts.append("ONNX Runtime + DirectML")
        if not parts:
            return "No GPU inference path detected on this machine."
        return "GPU paths available: " + ", ".join(parts)

    def diagnostic(self) -> str:
        """Verbose multi-line dump for stderr / a debug panel."""
        lines = [
            f"  mediapipe.tasks importable      : {self.mediapipe_tasks_importable}",
            f"  tasks GPU delegate enum present : {self.tasks_gpu_delegate_present}",
            f"  onnxruntime importable          : {self.onnxruntime_importable}",
            f"  onnxruntime DML provider listed : {self.onnxruntime_directml_provider}",
        ]
        if self.error_notes:
            lines.append("  errors during probe:")
            for note in self.error_notes:
                lines.append(f"    - {note}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def probe_gpu_paths() -> GpuProbeResult:
    """Cheap detection of GPU-capable inference backends. Cached so
    the Settings UI + the runtime loader can call it cheaply on
    every render. Repeated calls don't re-import or re-list
    providers."""
    errors: list[str] = []

    # 1. MediaPipe Tasks API path. Two-stage check:
    #    (a) the `vision` module + `Delegate.GPU` enum import
    #        cleanly. The enum has shipped in mediapipe for a
    #        while, so this passes on Windows even though the
    #        underlying delegate isn't supported.
    #    (b) try to actually serialize a BaseOptions(delegate=GPU)
    #        to its protobuf. The mediapipe Windows wheel raises
    #        `NotImplementedError("GPU Delegate is not yet
    #        supported for Windows")` here — which is what tells
    #        us the runtime can't actually use the delegate.
    # Without (b) the probe falsely reports "GPU available" on
    # Windows and the Settings toggle confuses the user.
    mp_tasks_importable = False
    tasks_gpu_present = False
    try:
        from mediapipe.tasks.python import vision as mp_vision  # noqa: F401
        from mediapipe.tasks.python.core.base_options import BaseOptions

        mp_tasks_importable = True
        delegate_attr = getattr(BaseOptions, "Delegate", None)
        delegate_gpu = getattr(delegate_attr, "GPU", None) if delegate_attr is not None else None
        if delegate_gpu is not None:
            try:
                # Construction-time validation of GPU delegate
                # support. We pass a deliberately bogus model
                # path because all we want is the to_pb2() call
                # to fire — that's the line that raises on
                # Windows. If it raises NotImplementedError, the
                # delegate isn't usable; if it raises any other
                # error (file not found etc.) the delegate IS
                # supported and we'd just hit the path-not-found
                # later, which is the OK case.
                opts = BaseOptions(model_asset_path="__probe__", delegate=delegate_gpu)
                opts.to_pb2()
                tasks_gpu_present = True
            except NotImplementedError as exc:
                errors.append(f"mediapipe.tasks GPU: {exc!s}"[:160])
            except Exception:
                # Any other exception means the delegate
                # serialised; the failure was downstream
                # (model_asset_path doesn't exist etc.) which is
                # fine for the probe — we only care whether the
                # delegate itself is supported.
                tasks_gpu_present = True
    except Exception as exc:
        errors.append(f"mediapipe.tasks: {type(exc).__name__}: {exc!s}"[:160])

    # 2. ONNX Runtime + DirectML provider path: importing
    # onnxruntime is cheap; listing available providers tells us
    # whether onnxruntime-directml is installed. The Cloudflare
    # Pages build doesn't include DirectML by default, so this
    # tends to be False on most user machines.
    ort_importable = False
    ort_dml = False
    try:
        import onnxruntime as ort

        ort_importable = True
        try:
            providers = list(ort.get_available_providers())
        except Exception:
            providers = []
        ort_dml = "DmlExecutionProvider" in providers
    except Exception as exc:
        errors.append(f"onnxruntime: {type(exc).__name__}: {exc!s}"[:160])

    return GpuProbeResult(
        mediapipe_tasks_importable=mp_tasks_importable,
        tasks_gpu_delegate_present=tasks_gpu_present,
        onnxruntime_importable=ort_importable,
        onnxruntime_directml_provider=ort_dml,
        error_notes=tuple(errors),
    )
