# GitHub Actions workflows

This directory contains GitHub Actions workflow files that provide
target-environment CI evidence for the bootstrap gate.

## Files

- `verify-windows-py314.yml` — Primary target-environment proof.
  Runs on `windows-latest` with Python 3.14. Runs the exact commands a
  human reviewer would run: `setup.ps1`, `test.ps1 -All -IncludePlaywright`,
  and the four direct commands (`ruff check`, `ruff format --check`,
  `pyright`, `pytest`). Also proves `ResourceWarning` is treated as error.

- `verify-linux.yml` — Secondary cross-version matrix. Runs on
  `ubuntu-latest` with Python 3.11, 3.12, 3.13, and 3.14. Same gate steps
  as the Windows workflow.

## Pushing these files

GitHub requires a PAT with the **Actions** permission (write) to push
commits that modify files under `.github/workflows/`. Fine-grained PATs
without this permission will get a 403 error:

```
remote: refusing to allow a Personal Access Token to create or update
workflow `.github/workflows/verify-linux.yml' without `workflow' scope
```

If you see this error, either:

1. Re-issue the PAT with the **Actions** permission (write), or
2. Push these files using a classic PAT with the `workflow` scope, or
3. Add them through the GitHub web UI (Actions tab → paste the content).

Reference copies of both workflow files are also kept at
`docs/ci/verify-windows-py314.yml.txt` and `docs/ci/verify-linux.yml.txt`
(these are pushable with any PAT that has Contents: write, because they're
not under `.github/workflows/`).
