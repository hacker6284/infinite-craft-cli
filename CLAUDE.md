# CLAUDE.md

## Release Process

**`main` is release-only.** Do not push to `main` until the change is ready to ship. There is no `[Unreleased]` staging section in `CHANGELOG.md` — every merge to `main` is a versioned release.

When landing a change on `main`:

1. **Determine the new version** — `git describe --tags --abbrev=0`, then bump semver (patch for fixes, minor for features, major for breaking changes)
2. **Update `CHANGELOG.md`** — add `## [X.Y.Z] - YYYY-MM-DD` at the top with the changes (never `[Unreleased]`)
3. **Commit, tag, and push** — `git tag vX.Y.Z`, then push `main` and the tag: `git push origin main && git push origin vX.Y.Z`

Work-in-progress stays on branches or worktrees until steps 1–3 are done.

The version in `pyproject.toml` is derived automatically from git tags via `hatch-vcs` — do not set it manually. The GitHub Actions workflow (`.github/workflows/publish.yml`) runs on `v*` tag pushes to publish PyPI and create a GitHub Release from the matching `CHANGELOG.md` section.
