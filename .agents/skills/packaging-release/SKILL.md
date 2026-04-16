# packaging-release

Use for runtime paths, installers, packaged asset access, platform-specific save paths, and release/build logic.

## Focus
- preserve source and packaged behavior
- use safe cross-platform path handling
- avoid hidden assumptions about working directory

## Must verify
- path logic works in development and packaged builds where applicable
- asset/save path changes do not break installers or runtime resolution
