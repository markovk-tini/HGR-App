"""Auto-update infrastructure for Touchless.

Three-piece flow:
  1. ReleaseChecker — fetches the latest GitHub release on a worker
     thread, compares versions, surfaces (version, body, asset_url)
     when a newer release exists.
  2. UpdateDialog — Qt dialog that shows the version, an expandable
     "What's new" section sourced from the GitHub release body, and
     either a Download/Later choice or an in-place progress bar
     while downloading the new installer.
  3. Updater — downloads the .exe to a temp location, then launches
     it with Inno Setup's silent flags before exiting the current
     app. Inno Setup handles closing the running process and
     replacing files in place.
"""
from .release_checker import ReleaseChecker, ReleaseInfo
from .updater import Updater

__all__ = ["ReleaseChecker", "ReleaseInfo", "Updater"]
