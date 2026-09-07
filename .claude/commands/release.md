---
description: Release a new version to GitHub + ClawHub
argument-hint: <semver, e.g. 0.3.0>
---

If `$1` is empty, stop and ask the user for the version. Do not proceed with an unset
version: `git tag v$1` would create and push a tag named literally `v`, and the git tag
is the only version anchor in this repo.

`$1` is bare semver (e.g. `0.3.0`). The `v` prefix is applied only to the git tag, not to the ClawHub version.

Release version `$1` by running these three steps in order. Stop and report immediately if any step fails — do not attempt to recover automatically.

1. Push `main`:

   ```bash
   git push origin main
   ```

2. Tag the release:

   ```bash
   git tag v$1 && git push --tags
   ```

3. Publish to ClawHub:

   ```bash
   npx clawhub@latest publish ./ --version $1
   ```

First-time ClawHub publish on a machine requires `npx clawhub@latest login` (browser auth, cached per machine). If step 3 fails with `Not logged in`, ask the user to run that login command, then retry only step 3.

If step 3 fails for any other reason after the tag is already pushed, fix the cause and re-run only step 3 with the same version. Do not force-overwrite the tag (`git tag -f`) without explicit user approval.

When all three steps succeed, report the released version and the outcome of each step.
