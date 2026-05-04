# Contributing to Touchless

Thanks for your interest in Touchless. This file covers two things you need
to know before opening a pull request:

1. The license your contribution will be released under
2. The Contributor License Agreement (CLA) you must accept

## License

Touchless is distributed under the
[Functional Source License v1.1 (FSL-1.1-Apache-2.0)](LICENSE). Your
contributions must be compatible with that license. By submitting a PR you
agree that your contribution may be distributed under FSL.

## Contributor License Agreement (CLA)

Touchless may be relicensed in the future (for example to enable a paid
tier or a dual-license commercial offering). For that to be legally clean,
the original author needs to retain the right to relicense the entire
codebase. To preserve that right while still letting you keep ownership
of your own work, every contributor must agree to the CLA below before
their pull request can be merged.

### CLA terms

By signing your commit (using `git commit -s`) or by stating "I have read
and agree to the CONTRIBUTING.md CLA" in the description of your pull
request, you affirm the following:

1. **You authored the contribution** or have the legal right to submit it
   under these terms.
2. **You grant Konstantin Markov a perpetual, worldwide, non-exclusive,
   royalty-free, irrevocable license** to use, reproduce, prepare
   derivative works of, publicly display, publicly perform, sublicense
   and distribute your contribution and derivative works thereof, under
   any license terms (including proprietary and commercial licenses).
3. **You retain copyright** in your original contribution. Nothing in
   this agreement transfers ownership of your code; you simply grant
   Konstantin Markov broad rights to use it.
4. **Your contribution is provided "AS IS"** without warranties of any
   kind.

This is a permissive CLA modeled after the Apache Individual CLA, simplified
for solo-maintainer projects.

### How to sign

Either:

- **Sign your commits**: `git commit -s -m "your message"` adds a
  `Signed-off-by: Your Name <your@email>` line that signals CLA acceptance,
  OR
- **State agreement in your PR description**: include the line
  `I have read and agree to the CONTRIBUTING.md CLA` somewhere in the PR
  body.

PRs without a signed-off CLA cannot be merged.

## Submitting a PR

1. Fork the repo
2. Create a feature branch off `main`
3. Make your changes
4. Run the existing test suite (`pytest tests/` from the repo root)
5. Open a PR against `main` with a clear description
6. Sign your commits OR add the CLA acceptance line to your PR body

## Code style

- Python: standard PEP 8, 4-space indent, `from __future__ import annotations`
  in all new files
- No tabs; spaces only
- Wrap long comments to ~80 columns where reasonable
- Don't add comments that just restate what the code does — only explain
  the WHY when it's non-obvious

## Questions

Open a GitHub Discussion or email the maintainer.

<!-- Author: Konstantin Markov -->
