# Deploy to Production

One-shot deploy: commit all changes, push, trigger workflow, and verify.

## Steps

1. **Check for changes:**
   Run `git -C /Users/ufuk/market-monitor status` to see what's modified.
   If nothing is modified, skip to step 5 (just trigger workflow).

2. **Commit:**
   Stage changed files (be specific, don't use `git add -A`).
   Write a concise commit message describing what changed.
   Commit from `/Users/ufuk/market-monitor`.

3. **Push:**
   Run `git -C /Users/ufuk/market-monitor pull --rebase origin main` first (GH Actions bot pushes data commits).
   Then `git -C /Users/ufuk/market-monitor push origin main`.

4. **Trigger signal generation:**
   Run `gh workflow run "Signal Alerts"` to regenerate signals.json with any code changes.
   Capture the run URL from the output.

5. **Watch and verify:**
   Run `gh run watch <run_id> --exit-status` in background.
   When complete, fetch `https://rapidsift.vercel.app/data/signals.json` and verify:
   - `generated_at` is fresh (within last few minutes)
   - Signal count is reasonable (300-600 for a 14-day window)
   - If new fields were added, spot-check they exist in the JSON

6. **Report:**
   Tell the user: commit hash, workflow status, signal count, and the live URL.
   If the workflow failed, show the last 30 lines of logs.

## Important

- Always commit from `/Users/ufuk/market-monitor` (the real repo, not a worktree)
- Always pull --rebase before push
- If push is rejected, pull --rebase and retry (once)
- The Vercel deploy is triggered automatically by the workflow's deploy hook step
