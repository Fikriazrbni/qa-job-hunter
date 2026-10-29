# GitHub Actions deployment

Use two repositories owned by the same user:

| Repository | Visibility | Contents |
| --- | --- | --- |
| `qa-job-hunter` | Public | Code, synthetic profile, tests, workflow template, docs |
| `qa-job-hunter-state` | **Private** | Actual profile, SQLite history, reports, active scheduled workflow |

The private workflow checks out the public code and its own state, runs the program, and commits changes to its private repository. Its repository-scoped `GITHUB_TOKEN` needs `contents: write`. No cross-repository write tokens, deploy keys, AI keys, or public report artifacts are used. Public CI only runs synthetic unit tests.

## Initial setup

1. Push the code to the public repository.
2. Run a local search with your actual `profile.json` if there is no existing database.
3. Create an empty **private** repository named `qa-job-hunter-state`.
4. Prepare a snapshot in an empty temporary directory:

   ```sh
   python3 scripts/bootstrap_state.py --destination /tmp/my-job-hunter-state
   ```

   The helper takes a consistent SQLite backup under the local run lock and copies the profile and reports. Existing deduplication and schedule anchors are retained. It refuses to overwrite a nonempty destination. It excludes the CV PDF and full manual history; imported job identities are already in SQLite.

5. Copy `deploy/job-hunter.yml` into the snapshot as `.github/workflows/job-hunter.yml`, initialize Git on `main`, and push to the private repository.
6. Set the private repository's Actions variable `CODE_REPOSITORY` to `YOUR_USERNAME/qa-job-hunter`.
7. Run **Job Hunter → Run workflow → tick** in its Actions tab. It waits until the saved due date or performs a due search. **status** checks the schedule without searching; **run** forces a search and resets the schedule anchor.
8. After verifying a cloud search and state persistence, disable the independent local scheduler:

   ```sh
   python3 -m jobhunter schedule uninstall
   ```

Keep the state repository private. Public Git ignores personal profiles, manual history, old skill assets, validation outputs, and reports. Do not force-add those files.

## Schedule and recovery

The workflow calls `tick` daily at **02:00 UTC = 09:00 WIB**. SQLite checks whether three calendar days have elapsed since the last completion. Do not use `*/3` in cron's day-of-month field: it resets at month boundaries. Change the interval in public `config.json`; if changing the hour, also change the private workflow cron.

Successful and partial searches advance the next due date. A complete source failure keeps the anchor unchanged and fails the workflow after saving state. The cloud timer retries on its next daily invocation; local 15-minute checks are separate. Manual `tick` is also available. A partial search saves its report and fails the workflow to expose reduced coverage.

Missing, incompatible, or corrupt state stops execution instead of silently resetting history. Workflow concurrency serializes manual and scheduled invocations. State push failures fail the job. Avoid manual edits to the state branch during a running search: conflicting pushes require intervention. Runner loss or cancellation before persistence can lose that run; the previous committed snapshot is the recovery point.

Reports remain private. Read `reports/latest.md` directly on GitHub, or download `reports/latest.html` and open it locally. Previous `run-*.html/json/md` reports and Git history are retained. Back up the private repository; SQLite is persistent state, not a disposable cache.

GitHub schedules can start late. The [60-day inactivity suspension for public repositories](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) does not apply to this private scheduled repository. Private Actions usage counts against your account's [included usage and billing settings](https://docs.github.com/en/billing/concepts/product-billing/github-actions). This project enables no paid runner or spending limit; exhausted account usage may stop execution.

To return to local execution, disable the private workflow first, download its latest database, profile, and reports to the corresponding local locations while the local scheduler is stopped, then run local `status` and reinstall the local scheduler. Independent databases do not share deduplication.
