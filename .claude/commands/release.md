---
description: Release a new version to GitHub + ClawHub
argument-hint: <semver, e.g. 0.3.0>
---

Release version `$1` by running these three commands in order. Stop and report immediately if any step fails — do not attempt to recover automatically.

```bash
git push origin main
git tag v$1 && git push --tags
clawhub publish ./ --version $1
```

`$1` is bare semver (e.g. `0.3.0`). The `v` prefix is applied only to the git tag, not to the ClawHub version.

If step 3 fails after the tag is already pushed, fix the cause and re-run only step 3 with the same version. Do not force-overwrite the tag (`git tag -f`) without explicit user approval.
