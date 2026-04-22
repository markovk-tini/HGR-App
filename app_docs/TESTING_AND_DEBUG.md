# Testing and Debug

## Default testing style

Use targeted verification tied to the user request, then expand outward to likely neighboring regressions.

## Test wave

The test wave should answer:
- does the exact requested behavior work?
- did any adjacent behavior regress?
- does the live camera still respond?
- do mouse and gesture states recover correctly after cancel/exit/error cases?
- does voice state recover correctly after follow-up prompts?

## Cleanup wave

The cleanup wave should answer:
- did the fix leave temporary prints, flags, or dead branches?
- are comments still accurate?
- are imports clean?
- is the diff smaller and clearer after cleanup?
- is the final summary precise about changed files and preserved behavior?

## Practical rule

Never stop at 'feature now works'.
A task is only done when it also survives the likely neighboring regressions.
