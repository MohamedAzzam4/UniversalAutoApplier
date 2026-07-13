#!/usr/bin/env bash
# Local verification script that mirrors the GitHub Actions Linux workflow.
# Run this on a Linux/macOS machine to produce the same evidence the CI
# would produce, without needing GitHub Actions.
#
# Usage:
#   ./scripts/verify_local.sh
#   PYTHON=/usr/bin/python3.12 ./scripts/verify_local.sh

set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
FAILURES=()

run_step() {
    local name="$1"
    shift
    echo ""
    echo "=== $name ==="
    if "$@"; then
        echo "PASS"
    else
        echo "FAIL (exit $?)"
        FAILURES+=("$name")
    fi
}

# Step 1: Verify LF line endings
run_step "1. Verify LF line endings" bash -c '
    files=("src/universal_auto_applier/__init__.py" "scripts/setup.sh" "tests/conftest.py")
    for f in "${files[@]}"; do
        # grep for carriage return (CR, 0x0D). If found, file has CRLF.
        if grep -Pq "\r" "$f"; then
            echo "FAIL: $f has CRLF line endings (expected LF)"
            exit 1
        fi
    done
    echo "  All checked files have LF line endings."
'

# Step 2: Clean environment
run_step "2. Remove stale .venv and .uaa_data" bash -c '
    rm -rf .venv .uaa_data .pytest_cache .ruff_cache .pyright
    echo "  Cleaned."
'

# Step 3: setup.sh
run_step "3. setup.sh" bash -c "
    PYTHON='$PYTHON' ./scripts/setup.sh
"

# Step 4: test.sh
run_step "4. test.sh (all gates including Playwright)" bash -c '
    RUN_ALL=1 INCLUDE_PLAYWRIGHT=1 ./scripts/test.sh
'

# Steps 5-8: direct commands
run_step "5. ruff check" bash -c './.venv/bin/python -m ruff check src tests migrations'
run_step "6. ruff format --check" bash -c './.venv/bin/python -m ruff format --check src tests migrations'
run_step "7. pyright" bash -c './.venv/bin/python -m pyright'
run_step "8. pytest (full suite)" bash -c './.venv/bin/python -m pytest'

# Step 9: contract tests specifically
run_step "9. contract tests (ResourceWarning gate)" bash -c './.venv/bin/python -m pytest tests/contract/test_migrations.py -q'

# Step 10: Prove ResourceWarning is treated as error
run_step "10. Prove ResourceWarning is treated as error" bash -c '
    temp_test="tests/test_rw_temp.py"
    cat > "$temp_test" << EOF
import warnings
def test_resourcewarning_is_error():
    warnings.warn("deliberate", ResourceWarning)
EOF

    # Use a trap to ALWAYS remove the temp test, even on interruption.
    trap "rm -f \"$temp_test\"" EXIT
    ./.venv/bin/python -m pytest "$temp_test" -q
    rw_exit=$?
    rm -f "$temp_test"
    trap - EXIT

    if [ "$rw_exit" -eq 0 ]; then
        echo "FAIL: ResourceWarning was NOT treated as error"
        exit 1
    fi
    echo "  ResourceWarning is correctly treated as error (test failed as expected)."
'

# Summary
echo ""
echo "=== SUMMARY ==="
echo "Python: $("$PYTHON" --version 2>&1)"
echo "Commit: $(git rev-parse HEAD)"
echo "Branch: $(git branch --show-current)"
if [ "${#FAILURES[@]}" -eq 0 ]; then
    echo "RESULT: ALL GATES PASSED"
    exit 0
else
    echo "RESULT: ${#FAILURES[@]} GATE(S) FAILED:"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
