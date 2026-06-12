# Releasing bitorm

bitorm is distributed straight from this Git repository — there is no PyPI publish step.
Consumers install a specific version by pinning to a Git **tag**:

```
uv add git+https://github.com/josephbrockw/bitorm@v1.2.0
```

So "cutting a release" means: bump the version, commit, tag, push the tag, and (optionally)
write a GitHub Release with notes.

## Version source of truth

The version lives in **one place**: the `version` field in `pyproject.toml`.

At runtime, `bitorm/__init__.py` reads it back from the installed package metadata
(`importlib.metadata.version("bitorm")`), so `bitorm.__version__`, `bitorm --version`, and
`python -m bitorm --version` always match `pyproject.toml`. **Do not** hardcode the version
anywhere else.

We follow [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.

- **PATCH** (1.2.0 → 1.2.1): bug fixes, no API change.
- **MINOR** (1.2.0 → 1.3.0): new, backwards-compatible features.
- **MAJOR** (1.2.0 → 2.0.0): breaking API changes.

The Git tag must match the `pyproject.toml` version, prefixed with `v` (e.g. `v1.2.0`).

## Release steps

1. **Make sure `main` is green.**
   ```
   uv run pytest tests/ -q
   ```

2. **Bump the version** in `pyproject.toml` (this is the only edit needed for versioning).
   ```toml
   # pyproject.toml
   version = "1.2.0"
   ```

3. **Sync and sanity-check** that the runtime version matches.
   ```
   uv sync
   uv run python -m bitorm --version    # should print: bitorm 1.2.0
   ```

4. **Commit the bump.**
   ```
   git commit -am "Release v1.2.0"
   ```

5. **Tag and push** (tag must equal the pyproject version, with a `v` prefix).
   ```
   git push origin main
   git tag v1.2.0
   git push origin v1.2.0
   ```

6. **Cut a GitHub Release** (optional but recommended). This wraps the tag with notes
   and a visible entry on the Releases page.
   ```
   gh release create v1.2.0 --title "v1.2.0" --generate-notes
   ```
   `--generate-notes` auto-builds a changelog from commits/PRs since the previous tag.
   Edit the notes afterward if you want a curated summary (see `--notes` to supply your own).

## Verifying a release

After pushing the tag, confirm a clean install resolves it:

```
# in a throwaway project
uv add git+https://github.com/josephbrockw/bitorm@v1.2.0
uv run bitorm --version    # should print: bitorm 1.2.0
```

## How consumers pin and upgrade

```
# pin to a release
uv add git+https://github.com/josephbrockw/bitorm@v1.2.0

# track the latest on the default branch instead of a tag
uv add git+https://github.com/josephbrockw/bitorm@main

# upgrade a branch pin to the newest commit
uv lock --upgrade-package bitorm
```

To move a tag pin to a newer release, bump the `@vX.Y.Z` in their `pyproject.toml` and
re-run `uv sync`.
