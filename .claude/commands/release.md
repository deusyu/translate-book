---
description: Release a new version to GitHub + ClawHub
argument-hint: <semver, e.g. 0.3.0>
---

If `$1` is empty, stop and ask the user for the version. Do not proceed with an unset
version: `git tag v$1` would create and push a tag named literally `v`, and the git tag
is the only version anchor in this repo.

`$1` is bare semver (e.g. `0.3.0`). The `v` prefix is applied only to the git tag, not to the ClawHub version.

Before releasing, list the open release blockers:

```bash
gh issue list --label release-blocker --state open
```

If the list is not empty, stop and show it to the user. Release only after those issues are closed, or if the user explicitly says to release anyway.

Then, still before step 1, prepare the release and confirm it with the user. None of this changes anything on GitHub or ClawHub; if a check fails, stop and show the user the output.

- `git branch --show-current` must print `main`, and `git status --porcelain` must print nothing. Step 2 tags the current commit, but step 3 publishes the files on disk.
- With your file-writing tool (not with `echo`, `printf` or a heredoc), write the changelog to a file outside the repository, such as `/tmp/translate-book-changelog.txt`, so that it neither dirties the worktree nor ends up in the package; `<changelog_file>` below stands for its path. The changelog is one or two plain-text sentences that tell people who install the skill what changed for them in this version, based on the pull requests merged since the previous tag. ClawHub shows it as the version's changelog, and the CLI cannot edit a changelog after publishing. Never publish while the file is missing, empty or holds only whitespace: ClawHub trims the changelog and generates its own from the changed files when nothing is left. The step 3 command does not run clawhub in those cases.
- Run the step 3 command with `--dry-run --json` added at the end. It uploads nothing and needs no login. In its JSON output, `folder` must be the absolute path of the repository root, which is the current directory, and `fileCount` must equal the number of files `git ls-files SKILL.md LICENSE scripts` lists. Equal counts do not prove that the file sets are the same. The clean-worktree check separately prevents uncommitted content from being published, so treat `fileCount` only as a sanity check.
- Show the user the version, `fileCount` and the output of `cat "<changelog_file>"`, and wait for the user to confirm.

Once the user confirms, release version `$1` by running these four steps in order. Stop and report immediately if any step fails — do not attempt to recover automatically.

1. Push `main`:

   ```bash
   git push origin main
   ```

2. Tag the release and push only that tag:

   ```bash
   git tag v$1 && git push origin v$1
   ```

   `git push --tags` pushes every local tag and fails when one of them differs from the tag of the same name on GitHub.

3. Publish to ClawHub:

   ```bash
   changelog_file="<changelog_file>"; grep -q '[^[:space:]]' "$changelog_file" && CLAWHUB_WORKDIR="$PWD" npx clawhub@latest publish ./ --slug translate-book --name translate-book --version $1 --changelog "$(cat "$changelog_file")"
   ```

   Without `--slug` and `--name`, ClawHub takes the slug and the display name from the current directory name, so publishing from a directory with another name, such as a worktree or a temporary clone, creates a separate skill or changes the display name of `translate-book`.

   `CLAWHUB_WORKDIR="$PWD"` makes clawhub resolve `./` against the current directory. Without it, on a machine with a default OpenClaw workspace configured, clawhub resolves `./` against that workspace, so the command fails or publishes the workspace as `translate-book`.

   `--changelog "$(cat "$changelog_file")"` passes the text of the changelog file the user confirmed. The shell does not expand text that reaches clawhub this way, so quotes, backticks, `$` and backslashes in it stay as they are.

   The path is written once, in `changelog_file`, so `grep` and `cat` read the same file. `grep -q '[^[:space:]]' "$changelog_file"` fails when the file is missing, empty or holds only whitespace, and `&&` then keeps clawhub from running, so the command fails. A missing file makes grep print an error and exit with 2; an empty or whitespace-only file makes it exit with 1 without printing anything. Run the line as a single shell command: if `changelog_file` is not set, for example because the assignment ran in an earlier shell call, grep gets an empty path, prints an error and exits with 2, and clawhub does not run.

4. Create the GitHub Release:

   ```bash
   gh release create v$1 --verify-tag --generate-notes
   ```

   `--verify-tag` stops the command if the tag `v$1` is not on GitHub, instead of creating the tag from the default branch. `--generate-notes` has GitHub write the title, which is the tag name, and the notes, which list the pull requests merged since the previous release. This step comes last because creating the release notifies people who watch the repository; if step 3 fails, nothing has been announced yet.

First-time ClawHub publish on a machine requires `npx clawhub@latest login` (browser auth, cached per machine). If step 3 fails with `Not logged in`, ask the user to run that login command, then retry step 3 and continue with step 4.

If step 3 fails for any other reason after the tag is already pushed, fix the cause, re-run step 3 with the same version, and continue with step 4. Do not force-overwrite the tag (`git tag -f`) without explicit user approval.

If step 4 fails, fix the cause and re-run only step 4. The tag is already on GitHub and the version is already on ClawHub, so do not repeat steps 1–3.

When all four steps succeed, report the released version and the outcome of each step.
