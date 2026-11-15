# Contributing to Jarvis

Thanks for helping. This is a personal project, so keep changes focused and small.

## Setup

```powershell
git clone https://github.com/SHIKHIL8137/open-jarvis.git
cd open-jarvis
uv sync --extra server --extra dev
cd frontend; npm install
```

You need Python 3.10-3.13, [uv](https://docs.astral.sh/uv/), Node.js 22.22+ (npm 11.19+) and, to run the
assistant, [Ollama](https://ollama.com/). See the README for the full quick start.

## Before you open a pull request

```powershell
uv run pytest tests/coding tests/tools tests/test_auto_router.py   # parts that changed
uv run ruff check src tests                                          # if you touch Python
cd frontend; npx tsc --noEmit                                        # if you touch the UI
```

- Add or update a test for any behavior you change. The coding agent's logic is tested with fake agents and
  reviewers, so tests do not call real models.
- Keep changes consistent with the surrounding code. Do not add features nobody asked for.
- Never commit API keys. Cloud keys belong in `~/.openjarvis/cloud-keys.env`, which is outside the repository.

## Pull requests

1. Branch from `main` with a short name.
2. One logical change per PR, with a clear description of what and why.
3. Link the issue it fixes, if there is one.

## Reporting bugs and ideas

Open an issue using the templates. For a bug include your OS, how you started Jarvis, which model you used and
the relevant lines from `~/.openjarvis/server.log` (remove anything private first).

## Security

Please report vulnerabilities privately by email to the maintainer through the address on the GitHub profile,
not in a public issue.

## License

By contributing you agree your contribution is licensed under the Apache License 2.0 (see LICENSE).
