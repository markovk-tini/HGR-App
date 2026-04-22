Drop PNG screenshots of the YouTube "Skip Ad" / "Skip" button into this folder.

How it's used
- When the user holds the "three apart" pose (three fingers extended and spread wide), the app grabs a screenshot of the focused Chrome YouTube window and runs cv2.matchTemplate against every *.png in this folder.
- Best match above 0.75 confidence triggers a SendCursorPos + left-click at the match center.
- Multiple PNGs are supported — useful for different locales, resolutions, or button variants (white pill, black pill, "Skip" vs "Skip Ad", etc.).

Capturing a template
1. Play a YouTube video that shows the Skip button.
2. Screenshot only the button (tight crop — just the pill, minimal surrounding pixels).
3. Save as a PNG in this folder. Any filename works.

Tips
- If matches are unreliable, add 2-3 variants (different videos / player sizes).
- If the Chrome window is a different DPI from the captured template, add a template at that DPI.
- Remove outdated templates if YouTube redesigns the button.
