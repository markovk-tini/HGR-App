# Release Process

Touchless ships through a hybrid model:
- **GitHub Releases** holds the version tag, release notes, and the
  small app-only update zip (~50–150 MB, fits under GitHub's 2GB
  asset cap).
- **Cloudflare** hosts the full installer (~2.4 GB) because GitHub
  rejects assets that big.

The auto-updater inside Touchless reads from GitHub's Releases API
and prefers the small zip when present. When a release ships a new
full installer (rare — only when ML stack or Python deps changed),
the developer attaches a marker to the release body that points at
the Cloudflare URL.

## Per-release checklist

1. **Update the version constants** to the new number (e.g. 1.0.6):
   - `src/hgr/__init__.py`     → `__version__ = "1.0.6"`
   - `installers/windows/hgr_app.iss` → `#define MyAppVersion "1.0.6"`

2. **Build both artifacts** locally:
   ```
   builder\windows\build_windows.bat
   ```
   Produces:
   - `release/Touchless_Installer.exe`           (full, 2.4 GB)
   - `release/Touchless_App_Update_1.0.6.zip`    (app-only, ~50 MB)

3. **Upload the full installer to Cloudflare**:
   - Drop `Touchless_Installer.exe` somewhere stable like
     `https://touchless.your-domain.com/v1.0.6/Touchless_Installer.exe`.
   - Verify the URL is publicly reachable with a HEAD request.

4. **Push the version-bump commit and tag** to GitHub:
   ```
   git tag v1.0.6
   git push origin master main v1.0.6
   ```

5. **Draft a GitHub Release**:
   - Go to https://github.com/markovk-tini/HGR-App/releases/new.
   - Choose tag `v1.0.6`.
   - Title: `Touchless 1.0.6` (or whatever).
   - Body: write the release notes in markdown. End with the
     installer markers:

     ```markdown
     ### What's new
     - Phone mic now uses the user gain setting...
     - QR button moved to the Microphone settings panel...
     - ...

     <!-- full-installer-url: https://touchless.your-domain.com/v1.0.6/Touchless_Installer.exe -->
     <!-- full-installer-size: 2576980378 -->
     ```

     The two markers are HTML comments — invisible to users
     reading the rendered notes, but parsed by the in-app
     auto-updater. The size is optional (in bytes) but improves
     the dialog from "Full update — large download" to "Full
     update — 2456 MB".

   - Attach **`Touchless_App_Update_1.0.6.zip`** as a release
     asset (just drag-drop into the Releases UI).
   - Do **not** attach the full `Touchless_Installer.exe` to the
     GitHub release — it's too big and lives on Cloudflare.

6. **Validate the release before publishing**:
   ```
   python tools/validate_release.py v1.0.6
   ```
   Checks that:
   - The source `__version__` matches the tag.
   - The release exists on GitHub.
   - The app-update zip is attached.
   - The installer marker is present and the Cloudflare URL is
     reachable.

7. **Publish**.

## What if I don't change ML deps for a release?

Then the full installer hasn't changed materially — you can keep
the previous Cloudflare URL marker pointing at the older `.exe`,
or omit the markers entirely. Auto-updater users will only see the
small app-zip path; new users downloading from your Cloudflare
landing page can grab whichever installer version you have up.

## What if the GitHub asset name differs?

The auto-updater looks for an asset whose name **starts with**
`Touchless_App_Update` and **ends with** `.zip`. So
`Touchless_App_Update_1.0.6.zip`,
`Touchless_App_Update_v1.0.6_x64.zip`, etc. all work. Keep the
prefix as-is — case-insensitive but otherwise exact.
