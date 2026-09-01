# Source-of-truth and agent context boundary

## Current production boundary

For the current production workflow, the effective executable source is the Python and shell content embedded in the heredocs of:

- `.github/workflows/probe.yml`
- `.github/workflows/build.yml`

The `src/` directory is a generated, readable snapshot. The build workflow rewrites parts of `src/` from its embedded heredoc on each run, so editing `src/` alone does not change production behavior.

## Safe change rule

- Production behavior change: update the owning workflow heredoc through an isolated branch and PR.
- Snapshot/documentation change: update `src/` or docs only when the workflow will intentionally regenerate the same result.
- Before any workflow change, extract the heredoc and compare it byte-for-byte with the intended snapshot.
- Do not run production Actions as a validation shortcut; use local tests or a Preview-like isolated run where available.
- Preserve current `data/`, `docs/`, and probe outputs; they are runtime artifacts, not source code.

## Generator limitation

Some legacy files under `tools/` read inputs from machine-local paths such as `/tmp/p1-sync` and `/tmp/p2-chart`. Those paths are not a portable source of truth. A future source-of-truth migration must first move these inputs into versioned repository paths or make the generator accept explicit, reproducible input arguments.

## Agent context boundary

The default Repomix context excludes large generated outputs via `.repomixignore`, but intentionally keeps workflow files and `src/` visible so agents can verify the production/snapshot relationship. Never use a reduced context that hides the workflow heredoc when reviewing a production behavior change.
