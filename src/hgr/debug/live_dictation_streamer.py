from __future__ import annotations

import json
import platform
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class LiveDictationEvent:
    event: str
    text: str = ""
    confidence: float = 0.0


class LiveDictationStreamer:
    def __init__(self) -> None:
        self._available = platform.system() == "Windows"
        self._message = "live dictation ready" if self._available else "live dictation unavailable on this platform"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def message(self) -> str:
        return self._message

    def stream(
        self,
        *,
        stop_event,
        event_callback: Callable[[LiveDictationEvent], None],
    ) -> bool:
        if not self._available:
            self._message = "live dictation unavailable on this platform"
            return False

        with tempfile.NamedTemporaryFile(prefix="hgr_live_dictation_", suffix=".stop", delete=False) as tmp:
            stop_path = Path(tmp.name)
        stop_path.unlink(missing_ok=True)
        with tempfile.NamedTemporaryFile(prefix="hgr_live_dictation_", suffix=".ps1", delete=False, mode="w", encoding="utf-8") as tmp_script:
            script_path = Path(tmp_script.name)
            tmp_script.write(self._script_text())

        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(stop_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def _watch_stop() -> None:
            stop_event.wait()
            try:
                stop_path.touch(exist_ok=True)
            except Exception:
                pass

        watcher = threading.Thread(target=_watch_stop, name="hgr-live-dictation-stop", daemon=True)
        watcher.start()

        try:
            if process.stdout is None:
                self._message = "live dictation stream unavailable"
                return False
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {"event": payload}
                if not isinstance(payload, dict):
                    continue
                event_name = str(payload.get("event", "") or "").strip().lower()
                if not event_name:
                    continue
                if event_name == "error":
                    self._message = str(payload.get("message", "live dictation error"))
                    event_callback(LiveDictationEvent(event="error", text=self._message))
                    return False
                event_callback(
                    LiveDictationEvent(
                        event=event_name,
                        text=str(payload.get("text", "") or ""),
                        confidence=float(payload.get("confidence", 0.0) or 0.0),
                    )
                )
            return_code = process.wait(timeout=5.0)
            if return_code != 0 and not stop_event.is_set():
                stderr_text = ""
                if process.stderr is not None:
                    stderr_text = process.stderr.read().strip()
                self._message = stderr_text or "live dictation process exited unexpectedly"
                return False
            self._message = "live dictation stopped"
            return True
        finally:
            try:
                stop_path.touch(exist_ok=True)
            except Exception:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
            stop_path.unlink(missing_ok=True)
            script_path.unlink(missing_ok=True)

    def _script_text(self) -> str:
        return r"""
$ErrorActionPreference = 'Stop'
$stopFile = $args[0]
function Emit-Event($eventName, $text = '', $confidence = 0.0, $message = '') {
    $payload = [PSCustomObject]@{
        event = $eventName
        text = $text
        confidence = [double]$confidence
        message = $message
    } | ConvertTo-Json -Compress
    [Console]::Out.WriteLine($payload)
    [Console]::Out.Flush()
}
try {
    Add-Type -AssemblyName System.Speech
    $culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-US')
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
    $grammar = New-Object System.Speech.Recognition.DictationGrammar
    $recognizer.LoadGrammar($grammar)
    $recognizer.SetInputToDefaultAudioDevice()
    # Disable auto-completion timeouts. TimeSpan.Zero means "no timeout",
    # so the engine never ends its Multiple-mode session on silence/babble
    # -- it only stops when we explicitly call RecognizeAsyncCancel.
    $recognizer.InitialSilenceTimeout = [TimeSpan]::Zero
    $recognizer.BabbleTimeout = [TimeSpan]::Zero
    $recognizer.EndSilenceTimeout = [TimeSpan]::FromSeconds(0.85)
    $recognizer.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromSeconds(1.10)
    $script:stopRequested = $false
    $recognizer.add_SpeechHypothesized({
        param($sender, $eventArgs)
        if ($null -ne $eventArgs.Result -and -not [string]::IsNullOrWhiteSpace($eventArgs.Result.Text)) {
            Emit-Event 'hypothesis' $eventArgs.Result.Text $eventArgs.Result.Confidence
        }
    })
    $recognizer.add_SpeechRecognized({
        param($sender, $eventArgs)
        if ($null -ne $eventArgs.Result -and -not [string]::IsNullOrWhiteSpace($eventArgs.Result.Text)) {
            Emit-Event 'final' $eventArgs.Result.Text $eventArgs.Result.Confidence
        }
    })
    $recognizer.add_SpeechRecognitionRejected({
        param($sender, $eventArgs)
        if ($null -ne $eventArgs.Result -and -not [string]::IsNullOrWhiteSpace($eventArgs.Result.Text)) {
            Emit-Event 'rejected' $eventArgs.Result.Text $eventArgs.Result.Confidence
        }
    })
    # Belt-and-suspenders: if the engine does end its session for any
    # reason we didn't anticipate, restart Multiple-mode recognition so
    # dictation stays alive until the user actually toggles it off.
    $recognizer.add_RecognizeCompleted({
        param($sender, $eventArgs)
        if (-not $script:stopRequested) {
            try {
                $sender.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
            } catch {
                Emit-Event 'error' '' 0.0 ("restart failed: " + $_.Exception.Message)
            }
        }
    })
    Emit-Event 'ready'
    $recognizer.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
    while (-not (Test-Path -LiteralPath $stopFile)) {
        Start-Sleep -Milliseconds 75
    }
    $script:stopRequested = $true
    $recognizer.RecognizeAsyncCancel()
    Start-Sleep -Milliseconds 150
    Emit-Event 'stopped'
} catch {
    Emit-Event 'error' '' 0.0 $_.Exception.Message
    exit 1
}
"""
