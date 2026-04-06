# CLAUDE.md

## Release Process

When completing a new feature, bug fix, or any change that should be released:

1. **Determine the new version** — check the latest tag with `git describe --tags --abbrev=0`, then bump following semver (patch for fixes, minor for features, major for breaking changes)
2. **Update `CHANGELOG.md`** — add a new `## [X.Y.Z] - YYYY-MM-DD` section at the top with the changes
3. **Tag the commit** — after committing, create a git tag: `git tag vX.Y.Z`

The version in `pyproject.toml` is derived automatically from git tags via `hatch-vcs` — do not set it manually. The GitHub Actions workflow (`.github/workflows/publish.yml`) uses the tag to trigger a PyPI publish and creates a GitHub Release with notes extracted from `CHANGELOG.md`.
