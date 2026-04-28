"""Custom-gesture feature: record a hand pose, bind it to an action, match
live hand input against saved poses, and execute the action on match.

This package is deliberately self-contained — it does NOT import from or
modify the rest of the HGR app. Integration with the running app happens
via a single future hook in the gesture pipeline; until that hook lands,
the feature lives entirely behind the standalone trainer/tester scripts
under tools/custom_gestures/.
"""
