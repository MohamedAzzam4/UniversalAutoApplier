# Checkpoint Policy (Mandatory for All AI Agents)

This document defines the mandatory checkpoint, session-start, and
git-safety rules that every AI agent (or human collaborator) MUST follow
when working in this repository. These rules exist because local-only
commits are not preserved across sandbox resets — work that exists only
on a local branch can be lost instantly.

## Session start (mandatory, before any code changes)

1. **Read first:** `AGENTS.md`, this file (`docs/development/CHECKPOINT_POLICY.md`),
   and `docs/handoffs/ACTIVE_WORKPACKAGE.md`. These three documents define
   the working contract.

2. **Fetch and verify:**
   ```text
   git fetch origin
   git rev-parse HEAD
   git rev-parse origin/<branch>
   ```
   Compare the two resolved values against the base SHA and last
   checkpoint SHA recorded in `ACTIVE_WORKPACKAGE.md`. They are reference
   points, not the current HEAD (the file cannot contain its own SHA).

3. **Verify working tree:** Run `git status --short` before any checkout
   or reset. Stop immediately if untracked user/debug files or uncommitted
   changes could be overwritten.

4. **Verify GitHub write authentication BEFORE making any code changes.**
   Public read access (fetch/clone) does NOT prove push access. A gateway
   proxy may intercept read operations without providing write auth. To
   verify write access without making a commit:
   ```text
   # Test write access by attempting a no-op push (dry-run):
   git push --dry-run origin <branch>
   ```
   If this fails with `Invalid username or token` or
   `Password authentication is not supported`, **stop before editing**
   and request a PAT from the user. Do not begin implementation.

5. **If write authentication is unavailable, stop.** Do not make code
   changes that you cannot push. Local-only commits are not preserved
   across sandbox resets.

## Checkpoint rule (work is not preserved until it is on origin)

- **Work is not considered preserved until its commit exists on origin.**
  A local commit that has not been pushed can be lost instantly if the
  sandbox is reset.

- **Commit and push after every completed milestone.** A milestone is a
  coherent unit of work (a feature, a fix, a test suite, a documentation
  update). Do not accumulate multiple milestones in a single local commit.

- **Commit and push before a pause, handoff, restart, risky operation,
  or expected context reset.** If you are about to lose context (e.g.,
  approaching a token limit, switching tasks, ending a session), push
  first.

- **Commit and push when context usage approaches 60%.** Do not wait
  until 70% or 80% — by then it may be too late to complete the push
  before the context runs out.

- **Do not leave important work uncommitted or in local-only commits.**
  If you have made code changes, commit and push them before doing
  anything else.

- **After every push, verify local HEAD equals origin checkpoint HEAD:**
  ```text
  git rev-parse HEAD
  git rev-parse origin/<branch>
  ```
  The two values MUST match. If they don't, the push failed — do not
  continue development.

- **Record both full SHAs in `ACTIVE_WORKPACKAGE.md`** after every
  successful push: the local HEAD SHA and the verified remote HEAD SHA.

- **If push fails, stop substantial development immediately.** Do not
  continue making code changes on top of an unpushed commit. Resolve
  the auth issue first (request a PAT), push, verify, then continue.

## Git safety (non-negotiable)

- **Never push directly to `main`.** All work arrives on `main` through
  a reviewed PR merge, never a direct push.
- **Never force-push.** Force-push rewrites history and can destroy
  collaborators' work. If a push is rejected, fetch and rebase instead.
- **Never delete checkpoint branches.** They preserve the history of
  each workpackage.
- **Use one checkpoint branch per workpackage.** Branch name format:
  `checkpoint/<workpackage-name>`.
- **Merge exactly once through a reviewed PR.** Never perform a local
  squash merge to main AND also merge the PR — that creates duplicate
  commits.
- **Preserve untracked user/debug files** unless explicitly authorized
  to delete them. `git status --short` before any destructive operation.
- **Never store credentials in the repository or remote URL.** Do not
  put tokens, API keys, or passwords in:
  - `remote.origin.url`
  - `.env` files committed to the repo
  - git config (local or global)
  - persistent credential files (`.git-credentials`)
  - any source file

  Use a temporary, one-shot credential helper that reads the token from
  an environment variable, and delete the helper immediately after the
  push. Never print or log the token.

## Temporary credential helper pattern

When a PAT is provided via an environment variable (e.g., `GITHUB_TOKEN`),
use this pattern to push without persisting the token:

```bash
# 1. Save the original remote URL
ORIGINAL_URL=$(git remote get-url origin)

# 2. Temporarily set the URL to NOT include any token
git remote set-url origin "https://github.com/<owner>/<repo>.git"

# 3. Create a temporary credential helper in /tmp (NOT in the repo)
cat > /tmp/git-cred-helper-oneshot.sh << 'EOF'
#!/bin/bash
if [ -z "$GITHUB_TOKEN" ]; then exit 1; fi
echo "username=x-access-token"
echo "password=$GITHUB_TOKEN"
EOF
chmod 700 /tmp/git-cred-helper-oneshot.sh

# 4. Push using the helper (token via env var, never written to config)
GITHUB_TOKEN='<token>' git -c credential.helper="!/tmp/git-cred-helper-oneshot.sh" push origin <branch>

# 5. Immediately delete the helper and restore the original URL
rm -f /tmp/git-cred-helper-oneshot.sh
git remote set-url origin "$ORIGINAL_URL"

# 6. Verify no traces remain
git config --local --list | grep credential
ls /tmp/git-cred-helper-oneshot.sh 2>/dev/null
```

## ACTIVE_WORKPACKAGE.md required content

`docs/handoffs/ACTIVE_WORKPACKAGE.md` must always contain:

- **Repository:** the GitHub `owner/repo` identifier
- **Workpackage:** the WP ID (e.g., WQ-6)
- **Branch:** the checkpoint branch name
- **Base SHA:** the `origin/main` SHA the branch was created from
- **Local HEAD:** the current local HEAD SHA (resolved dynamically, never embedded in the file's own commit)
- **Verified remote HEAD:** the `origin/<branch>` SHA after the last successful push
- **Last successful checkpoint time:** ISO 8601 timestamp of the last push
- **Completed milestones:** bullet list of what has been done
- **Changed files:** list of files modified in this workpackage
- **Validation results:** ruff, pyright, pytest counts
- **Remaining work:** bullet list of what is left to do
- **Blockers:** anything blocking progress
- **Exact next action:** the single next step to take

## Enforcement

These rules are enforced by the project's review process. A PR that
contains local-only commits (not pushed), credentials in files, or
force-push history will be rejected. An AI agent that ignores these
rules risks losing all its work to a sandbox reset.
