# Contributing to OpenProgram

OpenProgram is an open-source self-programming AI agent framework. Contributions
to the runtime, providers, interfaces, documentation, examples, and agent
programs are welcome.

## Before opening an issue

- Ask usage and design questions in [GitHub Discussions](https://github.com/Fzkuji/OpenProgram/discussions).
- Search [existing issues](https://github.com/Fzkuji/OpenProgram/issues) before reporting a duplicate.
- Report vulnerabilities privately through the repository's [Security Advisories](https://github.com/Fzkuji/OpenProgram/security/advisories/new), not in a public issue.

## Development setup

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
uv sync --locked --extra dev
```

The [documentation](https://openprogram.io/docs/) covers the architecture,
public APIs, installation profiles, and current capabilities.

## Pull requests

1. Create a focused branch from `main`.
2. Add or update the smallest test that proves the behavior.
3. Run the relevant targeted tests while developing.
4. Before requesting review, run:

   ```bash
   uv run --locked --extra dev ruff check openprogram tests scripts tools
   uv run --locked --extra dev python -m pytest -q tests/contracts
   uv run --locked --extra dev python -m pytest -q tests/unit
   uv run --locked --extra dev python -m pytest -q tests/component
   uv run --locked --extra dev python -m pytest -q tests/integration
   uv run --locked --extra dev python -m pytest -q -m "not browser" tests/e2e
   uv run --locked --with markdown-it-py --with mdit-py-plugins --with pygments python -m tools.docs_site.build
   uv run --locked --with markdown-it-py --with mdit-py-plugins --with pygments python -m tools.docs_site.check_landing
   uv run --locked --with markdown-it-py --with mdit-py-plugins --with pygments python -m tools.docs_site.checklinks
   ```

5. For Web changes, also run from `apps/web/`:

   ```bash
   npm ci
   npm test
   npx tsc --noEmit
   npm run check
   npm run build
   ```

6. For CLI changes, run from `apps/cli/`:

   ```bash
   npm ci
   npm run typecheck
   npm test
   npm run build
   ```

7. For desktop changes, run from `apps/desktop/`:

   ```bash
   npm ci
   npm run check
   ```

8. For built-Web or browser changes, build Web first and run the dedicated
   browser selection from the repository root:

   ```bash
   uv sync --locked --extra dev --extra browser
   uv run --locked --extra dev --extra browser playwright install --with-deps chromium
   npm --prefix apps/web ci
   npm --prefix apps/web run build
   uv run --locked --extra dev --extra browser python -m pytest -q -m browser tests/e2e/web
   ```

9. Generate the same serial unit coverage report as CI when reviewing test
   coverage. CI and the local command enforce the verified 40% floor while
   retaining the XML report:

   ```bash
   uv run --locked --extra dev coverage run --branch --source=openprogram -m pytest -q tests/unit
   uv run --locked --extra dev coverage xml -o coverage.xml
   uv run --locked --extra dev coverage report --show-missing --precision=6 --fail-under=40
   ```

10. For changes to test infrastructure, shared fixtures, or process state,
    verify the required Python selection three consecutive times with the fixed
    worker count used by the test-system stability gate:

    ```bash
    for run in 1 2 3; do
      uv run --locked --extra dev python -m pytest -q -n 4 tests/contracts tests/unit tests/component tests/integration tests/e2e || exit 1
    done
    ```

External-service tests under `tests/live/` are not part of ordinary pull
request checks. Run them explicitly only with the required credentials and
network access.

Keep each pull request limited to one coherent change. Explain the user-visible
behavior, verification performed, and any compatibility or migration impact.

## Code and documentation style

- Follow the patterns in the surrounding module before introducing a new abstraction.
- Use type hints and document public APIs.
- Keep documentation examples executable and use canonical `openprogram.io` and
  `github.com/Fzkuji/OpenProgram` links.
- Do not commit credentials, private logs, generated build directories, or local paths.

By participating, you agree that your contribution is licensed under the
repository's [AGPL-3.0 license](LICENSE).
