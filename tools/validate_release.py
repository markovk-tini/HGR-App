"""Sanity-check a Touchless GitHub release before publishing.

Catches the failure modes that would silently break the in-app
auto-updater for thousands of users:
  - Missing or unparseable full-installer URL marker when no .exe
    asset is attached to the release.
  - Cloudflare URL that 404s or redirects oddly.
  - Mismatched __version__ in the running source vs the release tag.
  - Missing or wrong-named app-update zip asset.

Usage:
    python tools/validate_release.py <tag>            # check published
    python tools/validate_release.py --draft <body>   # dry-run a body string

Examples:
    python tools/validate_release.py v1.0.6
    python tools/validate_release.py --draft "Release notes here\\n
        <!-- full-installer-url: https://touchless.example.com/v1.0.6/Touchless_Installer.exe -->\\n
        <!-- full-installer-size: 2576980378 -->"

Returns exit code 0 on pass, 1 on any check failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GITHUB_RELEASE_URL = "https://api.github.com/repos/markovk-tini/HGR-App/releases/tags/{tag}"
INSTALLER_ASSET_NAME = "Touchless_Installer.exe"
APP_UPDATE_ZIP_PREFIX = "Touchless_App_Update"

PASS = "[OK]   "
FAIL = "[FAIL] "
WARN = "[WARN] "


def read_source_version() -> str:
    init_file = ROOT / "src" / "hgr" / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1)


def fetch_release(tag: str) -> dict:
    url = GITHUB_RELEASE_URL.format(tag=tag)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Touchless-ReleaseValidator/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def head_check(url: str) -> tuple[bool, int, str]:
    """Returns (ok, status_code, message). Some CDNs reject HEAD, so
    we fall back to a 1-byte ranged GET if the HEAD is rejected."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            return (True, resp.status, "HEAD ok")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405):
            try:
                req = urllib.request.Request(
                    url, headers={"Range": "bytes=0-0"}, method="GET"
                )
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    return (True, resp.status, "GET range ok")
            except Exception as inner:
                return (False, 0, f"GET range failed: {inner!s}")
        return (False, exc.code, f"HEAD HTTP {exc.code}")
    except Exception as exc:
        return (False, 0, f"HEAD error: {exc!s}")


def check_body(body: str) -> tuple[bool, str, int]:
    """Validate the marker conventions in the release body."""
    url_re = re.compile(r"<!--\s*full-installer-url:\s*(https?://\S+?)\s*-->", re.IGNORECASE)
    size_re = re.compile(r"<!--\s*full-installer-size:\s*(\d+)\s*-->", re.IGNORECASE)
    url_match = url_re.search(body)
    size_match = size_re.search(body)
    url = url_match.group(1).strip() if url_match else ""
    size = int(size_match.group(1)) if size_match else 0
    return (bool(url), url, size)


def validate_published(tag: str) -> int:
    print(f"Validating release tag: {tag}")
    print("-" * 56)
    fails = 0

    # 1. Source version matches tag
    source_version = read_source_version()
    tag_version = re.sub(r"^v", "", tag, flags=re.IGNORECASE)
    if source_version == tag_version:
        print(f"{PASS} Source __version__ ({source_version}) matches tag ({tag_version})")
    else:
        print(f"{FAIL} Source __version__ ({source_version}) does not match tag ({tag_version})")
        fails += 1

    # 2. Fetch release from GitHub
    try:
        release = fetch_release(tag)
    except urllib.error.HTTPError as exc:
        print(f"{FAIL} Couldn't fetch release: HTTP {exc.code}")
        return 1
    except Exception as exc:
        print(f"{FAIL} Couldn't fetch release: {exc!s}")
        return 1
    print(f"{PASS} Release fetched from GitHub")

    body = str(release.get("body") or "").strip()
    assets = release.get("assets") or []

    # 3. Check assets
    has_installer_asset = any(
        str(a.get("name") or "").lower() == INSTALLER_ASSET_NAME.lower()
        for a in assets
    )
    has_zip_asset = any(
        str(a.get("name") or "").lower().startswith(APP_UPDATE_ZIP_PREFIX.lower())
        and str(a.get("name") or "").lower().endswith(".zip")
        for a in assets
    )

    if has_zip_asset:
        print(f"{PASS} App-update zip asset attached")
    else:
        print(f"{WARN} No app-update zip asset — auto-updater will fall back to full installer")

    if has_installer_asset:
        print(f"{PASS} Full installer asset attached on GitHub")
    else:
        # 4. Need a body marker pointing at the external installer
        has_marker, url, size = check_body(body)
        if has_marker:
            print(f"{PASS} External installer URL in body: {url}")
            if size > 0:
                mb = size / (1024 * 1024)
                print(f"{PASS} Size hint: {size} bytes ({mb:.1f} MB)")
            else:
                print(f"{WARN} No size hint — dialog will say 'large download' without a number")
            ok, code, msg = head_check(url)
            if ok:
                print(f"{PASS} Installer URL reachable ({msg})")
            else:
                print(f"{FAIL} Installer URL unreachable: {msg}")
                fails += 1
        else:
            if has_zip_asset:
                print(
                    f"{WARN} No full installer asset and no <!-- full-installer-url: ... --> marker. "
                    f"Existing users will only see the app-zip update path."
                )
            else:
                print(
                    f"{FAIL} Release has neither an installer asset nor a body marker, "
                    f"and no app-update zip. The auto-updater can't do anything with this release."
                )
                fails += 1

    print("-" * 56)
    if fails:
        print(f"{FAIL} {fails} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


def validate_draft(body: str) -> int:
    print("Dry-run on draft release body")
    print("-" * 56)
    has_marker, url, size = check_body(body)
    if not has_marker:
        print(
            f"{FAIL} No <!-- full-installer-url: ... --> marker found. "
            f"If you're hosting the .exe on Cloudflare, add this to the body:"
        )
        print('       <!-- full-installer-url: https://your-cdn/v1.0.6/Touchless_Installer.exe -->')
        return 1
    print(f"{PASS} Marker URL: {url}")
    if size > 0:
        print(f"{PASS} Size hint: {size} bytes ({size / (1024 * 1024):.1f} MB)")
    ok, code, msg = head_check(url)
    if ok:
        print(f"{PASS} URL reachable ({msg})")
        return 0
    print(f"{FAIL} URL unreachable: {msg}")
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tag_or_draft", nargs="?", help="Release tag (e.g. v1.0.6)")
    parser.add_argument("--draft", help="Validate a draft release body string instead of fetching")
    args = parser.parse_args(argv)

    if args.draft:
        return validate_draft(args.draft)
    if not args.tag_or_draft:
        parser.print_help()
        return 1
    return validate_published(args.tag_or_draft)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
