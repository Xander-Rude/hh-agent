# CI/CD

HH Agent uses a pull-request-first delivery flow for changes to `main`.

## Flow

```text
feature branch
    ↓
pull request
    ↓
GitHub Actions CI
    ↓
merge to main
    ↓
GitHub Actions CI on main
    ↓
GitHub Actions CD
    ↓
Octopus Deploy release
    ↓
Production deployment to HH-Agent-PC
    ↓
C:\hh-agent updated with fast-forward-only Git pull
```

## CI

The `CI` workflow runs on pull requests to `main` and on pushes to `main`.
It currently performs whitespace checks, Python compilation and core unit tests on Windows / Python 3.12.

## CD

The `CD` workflow is triggered only after a successful `CI` run on `main`.
It creates a new release in the Octopus Deploy project `HH Agent` and requests deployment to the `Production` environment.

The deployment target is the Windows machine `HH-Agent-PC`, connected through a Polling Tentacle.

The Octopus deployment script:

- never starts Pipeline or Apply;
- refuses to update while Pipeline or Apply is running;
- requires the local repository to be on `main`;
- requires tracked working-tree changes to be absent;
- fetches `origin/main` and allows only fast-forward updates;
- runs Python sanity checks after the update;
- restarts Telegram components only when relevant files changed;
- resets the repository to the previous revision if post-update validation fails.

## Operational note

A successful GitHub `CD` job confirms that GitHub successfully created/requested the Octopus deployment. The final deployment result must be checked in Octopus, because the target-side deployment can still fail after the GitHub workflow has completed.
