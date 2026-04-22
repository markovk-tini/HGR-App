"""Run llama-server grammar correction against fixture files and print a diff report.

Usage:
    python tests/dictation_correction/run_correction.py [fixture_dir]

Boots the LlamaServer once, runs every *.txt in the fixture dir through
LlamaServer.correct(), and prints original/corrected/length-ratio/word-delta
for each. Use this to iterate on the system prompt and on the apply-side
sanity guards without restarting the full HGR app.
"""
from __future__ import annotations

import difflib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hgr.voice.llama_server import LlamaServer  # noqa: E402


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _length_ratio(original: str, corrected: str) -> float:
    if not original:
        return 1.0
    return len(corrected) / len(original)


def _word_delta(original: str, corrected: str) -> int:
    return _word_count(corrected) - _word_count(original)


def _inline_diff(original: str, corrected: str) -> str:
    matcher = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.append(corrected[j1:j2])
        elif tag == "delete":
            out.append(f"[-{original[i1:i2]}-]")
        elif tag == "insert":
            out.append(f"[+{corrected[j1:j2]}+]")
        elif tag == "replace":
            out.append(f"[-{original[i1:i2]}-][+{corrected[j1:j2]}+]")
    return "".join(out)


def _verdict(original: str, corrected: str) -> str:
    if corrected.strip() == original.strip():
        return "UNCHANGED"
    ratio = _length_ratio(original, corrected)
    word_delta = _word_delta(original, corrected)
    if ratio < 0.4:
        return f"REJECT (corrected too short, ratio={ratio:.2f})"
    if ratio > 1.6:
        return f"REJECT (corrected too long, ratio={ratio:.2f})"
    if word_delta > 6:
        return f"REJECT (added {word_delta} words)"
    return "ACCEPT"


def main(argv: list[str]) -> int:
    fixture_dir = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "fixtures"
    if not fixture_dir.exists():
        print(f"fixture dir not found: {fixture_dir}", file=sys.stderr)
        return 1

    fixtures = sorted(p for p in fixture_dir.glob("*.txt"))
    if not fixtures:
        print(f"no .txt files in {fixture_dir}", file=sys.stderr)
        return 1

    print(f"Booting LlamaServer (this can take 5-15s on first launch)...")
    server = LlamaServer()
    if not server.available:
        print(f"LlamaServer unavailable: {server.message}", file=sys.stderr)
        return 1
    if not server.start():
        print(f"LlamaServer failed to start: {server.message}", file=sys.stderr)
        return 1
    print(f"LlamaServer ready: backend={server.backend} message={server.message}")
    print()

    summary: list[tuple[str, str, float, int, float, str]] = []
    try:
        for path in fixtures:
            original = path.read_text(encoding="utf-8").strip()
            print("=" * 80)
            print(f"FIXTURE: {path.name}")
            print(f"ORIGINAL  ({len(original)} chars, {_word_count(original)} words):")
            print(f"  {original}")
            t0 = time.monotonic()
            corrected = server.correct(original)
            latency = time.monotonic() - t0
            if corrected is None:
                print(f"CORRECTED: <none returned>  ({latency:.2f}s)")
                summary.append((path.name, "ERROR", 0.0, 0, latency, "no output"))
                print()
                continue
            corrected = corrected.strip()
            ratio = _length_ratio(original, corrected)
            word_delta = _word_delta(original, corrected)
            verdict = _verdict(original, corrected)
            print(f"CORRECTED ({len(corrected)} chars, {_word_count(corrected)} words, {latency:.2f}s, ratio={ratio:.2f}, word_delta={word_delta:+d}):")
            print(f"  {corrected}")
            print(f"DIFF:")
            print(f"  {_inline_diff(original, corrected)}")
            print(f"VERDICT: {verdict}")
            print()
            summary.append((path.name, verdict.split()[0], ratio, word_delta, latency, corrected))
    finally:
        print("Stopping LlamaServer...")
        server.stop()

    print("=" * 80)
    print("SUMMARY")
    print(f"{'fixture':<40} {'verdict':<12} {'ratio':>6} {'wdelta':>7} {'latency':>8}")
    for name, verdict, ratio, word_delta, latency, _ in summary:
        print(f"{name:<40} {verdict:<12} {ratio:>6.2f} {word_delta:>+7d} {latency:>7.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
