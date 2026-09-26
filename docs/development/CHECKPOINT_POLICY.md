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
   Compare the resolved values with the actual source/integration parent and
   last checkpoint recorded in `ACTIVE_WORKPACKAGE.md`. The base need not be
   `origin/main`; those SHAs are reference points, not the current HEAD.

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

- **Preservation and acceptance are separate.** Work is preserved only when
  its commit exists on origin. A clearly labelled WIP checkpoint may record
  known failures or unverified gates before a long run, pause or context reset;
  WIP status is not acceptance.

- **Commit and push after each publishable milestone**, unless a supervisor
  explicitly reserved the draft for review first. A milestone is a coherent
  unit of work; do not accumulate unrelated milestones in one commit.

- **Before a pause, handoff, restart, risky operation or expected context
  reset, preserve the work when publication is authorized.** If a supervisor
  explicitly reserved a draft for review, keep it labelled WIP and do not
  publish it prematurely.

- **Commit and push when context usage approaches 60%.** Do not wait
  until 70% or 80% — by then it may be too late to complete the push
  before the context runs out.

- **Do not leave accepted work only in a local commit.** If a change is still
  a supervisor-reserved draft, label it WIP and keep it available for that
  review; publish only after that review permits it.

- **After every push, verify local HEAD equals the remote branch HEAD:**
  ```text
  git rev-parse HEAD
  git rev-parse origin/<branch>
  ```
  The two values MUST match. If they don't, the push failed — do not
  continue development.

- **Record dynamic verification in `ACTIVE_WORKPACKAGE.md`** after every
  successful push. Do not embed the SHA of the commit that contains that
  handoff. For unpublished WIP, state that it is unpublished; do not claim
  local/remote equality. Resolve SHA values from command output and do not
  create a status-only commit just to record its own SHA.

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
- **Use one integration branch per workpackage.** Follow the assigned
  `checkpoint/<workpackage-name>` or `codex/<workpackage-name>` name. Optional
  bounded worktrees are for independent paths only; name one integration
  owner and do not let parallel workstreams rewrite shared state independently.
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
- **Base SHA:** the actual source/integration parent SHA the branch was created from; it need not be `origin/main`
- **Local HEAD:** the current local HEAD SHA (resolved dynamically, never embedded in the file's own commit)
- **Verified remote HEAD:** resolve `origin/<branch>` dynamically after publication; label unpublished WIP clearly
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
