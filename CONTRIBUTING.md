# Contributing to ModelDiff

Thanks for your interest in improving ModelDiff. This guide covers setting up the project, the expectations for changes, and how to pick up work.

## Setup

You need Python 3.11 or newer and Git.

```bash
git clone https://github.com/OpenWave-India/modeldiff.git
cd modeldiff
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,memory]"
```

## Running tests and checks

```bash
pytest                                   # full test suite, runs offline in a few seconds
pytest tests/test_checks.py -k tool_call # a subset
ruff check src tests examples            # lint
ruff format src tests examples           # format
```

CI runs the same commands on Linux, macOS, and Windows with Python 3.11 to 3.13. Please run them locally before opening a pull request.

To try the CLI end to end:

```bash
modeldiff run examples/quickstart/suite.yaml -c examples/quickstart/configs/baseline.yaml -o out/b.json --plugin examples/quickstart/checks.py
modeldiff run examples/quickstart/suite.yaml -c examples/quickstart/configs/candidate.yaml -o out/c.json --plugin examples/quickstart/checks.py
modeldiff compare out/b.json out/c.json
```

## Expectations for changes

- **Tests test behavior.** Assert on outcomes and messages, not only that a function exists or returns something. Every bug fix comes with a test that fails without the fix.
- **Offline and deterministic.** Tests must not access the network, download models, or depend on timing. Use the replay provider, an in-memory provider (see `tests/test_runner_and_results.py`), or `monkeypatch` for I/O.
- **Error messages help the user fix the problem.** Name the file, the case, and the field, and say what was expected.
- **Keep it small.** Prefer the standard library. A new dependency needs a reason in `docs/architecture.md`, and should be an optional extra if only one feature uses it.
- **Type hints** on public functions and classes.
- **Documentation.** Update the README when you change CLI flags, file formats, or check behavior.
- **File-format reference.** See [docs/file-formats.md](docs/file-formats.md) for the documented fields and compatibility details of ModelDiff's file formats.
- **Accurate claims.** Do not describe features, metrics, or benchmarks that the code does not implement.

## Picking up an issue

1. Look for issues labelled [`good first issue`](../../issues?q=is%3Aopen+label%3A%22good+first+issue%22) or [`help wanted`](../../issues?q=is%3Aopen+label%3A%22help+wanted%22).
2. Comment on the issue to say you are working on it, so others do not duplicate the work. If you stop, say so, so that someone else can pick it up.
3. Ask questions on the issue if anything is unclear. Every issue lists acceptance criteria, which are what reviewers will check.

Please comment first and wait for a maintainer to assign the issue to you before starting.

### One issue at a time

To give everyone a chance, especially people making their first contribution:

- Work on **one issue at a time**. Claim the next one after your pull request is merged.
- Issues labelled `good first issue` are for **first-time contributors**. After your first merged pull request, please leave them for newcomers and pick `help wanted` or other issues instead.
- Pull requests for issues that weren't assigned to you, or beyond the one-at-a-time limit, may be closed so others can take the issue.

## Branches and pull requests

- Fork the repository, then branch from `main` using a short descriptive name: `fix/required-fields-duplicates`, `feat/junit-report`, `docs/ollama-setup`.
- Keep each pull request to one logical change. Mention the issue it resolves (`Closes #12`).
- Write commit messages in the imperative mood ("Add JUnit report format").
- Fill in the pull request template, including how you tested the change.
- CI must pass. A maintainer will review your pull request; please respond to review comments with new commits rather than force-pushing over them while review is in progress.

## Proposing new checks, metrics, or providers

Open an issue using the feature request template before writing a large change. Describe:

- the regression it would catch, with a concrete example of output that should pass and output that should fail;
- why a custom check (`--plugin`) is not enough, for check proposals;
- for metrics: exactly what is measured, on which process or device, and how noisy it is;
- for providers: the runtime, how it would be tested without a running server, and any new dependency.

ModelDiff aims to stay deterministic and offline-testable. Proposals that require a hosted API or a downloaded model in the test suite will not be accepted. Proposals for model-graded or embedding-based scoring need a documented and reproducible method.

See [docs/architecture.md](docs/architecture.md) for where checks and providers live.

## Recognition

Once your first pull request is merged, you are added to the Contributors section of the README and credited in [CHANGELOG.md](CHANGELOG.md) and the release notes for the next version.

## Code of Conduct

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
