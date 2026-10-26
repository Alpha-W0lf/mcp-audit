# Releasing to PyPI

The PyPI name is `mcp-audit-kit` (PyPI refuses `mcp-audit`: it is too similar to the existing
`mcpaudit`). The import package and command stay `mcp_audit` and `mcp-audit`.

Releases publish from GitHub Actions with PyPI trusted publishing (OIDC): no
token is stored anywhere. Pushing a tag `vX.Y.Z` runs `release.yml`, which
reruns CI, checks the tag equals the package version, builds the
sdist and wheel, runs `twine check --strict`, and publishes.

## One-time setup (the package owner)

1. Sign in at <https://pypi.org> (create an account, verify the email and turn on 2FA if needed).
2. Open <https://pypi.org/manage/account/publishing/>.
3. Under "Add a new pending publisher", choose the GitHub tab and enter:
   PyPI project name `mcp-audit-kit`, owner `Alpha-W0lf`, repository `mcp-audit`,
   workflow name `release.yml`, environment name `pypi`.
4. Click "Add". The project is created on the first successful publish.
5. On GitHub, open the repo, Settings, Environments, New environment, name it
   `pypi`, and save. Optionally add yourself under "Required reviewers" so each
   publish waits for your approval.

## Each release

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new version heading,
   and set `__version__` in `mcp_audit/__init__.py` to match (the one place the version lives).
   Merge to `main` once CI is green.
2. Tag and push (from an up-to-date `main`):

   ```console
   git tag v0.5.1 && git push origin v0.5.1
   ```

3. Watch the "Release" run in the Actions tab. Approve the `pypi` deployment if
   you added a reviewer.
4. Confirm: `pipx run --spec mcp-audit-kit mcp-audit list-checks` (or `pip install mcp-audit-kit`) works.

A version can never be re-uploaded to PyPI. If a release fails after upload,
fix forward with the next version number.
