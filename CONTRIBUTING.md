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
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The [documentation](https://openprogram.io/docs/) covers the architecture,
public APIs, installation profiles, and current capabilities.

## Pull requests

1. Create a focused branch from `main`.
2. Add or update the smallest test that proves the behavior.
3. Run the relevant targeted tests while developing.
4. Before requesting review, run:

   ```bash
   python -m pytest tests/ --ignore=tests/integration
   python -m tools.docs_site.build
   python -m tools.docs_site.check_landing
   python -m tools.docs_site.checklinks
   ```

5. For Web changes, also run from `web/`:

   ```bash
   npm ci
   npx tsc --noEmit
   npm run build
   npm run check
   ```

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
