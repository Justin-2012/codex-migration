# Development and contribution

Use synthetic data only; read AGENTS.md before editing. The public repository never includes personal migration data. Describe behavior changes and actual checks in pull requests.

## Local unit tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Python 3.11+ is required for development. These tests use fixtures and do not establish native macOS client support. CI runs the unit suite on macOS; no credentials, actual chat profiles or user data are used.

## Native integration

Only use a macOS arm64 machine with a tested client and matching CLI. Read tools/integration.py before execution. It creates temporary source/destination profiles and uses metadata-only native calls, not model turns:

```sh
PYTHONPATH=src .venv/bin/python tools/integration.py --source-version 59 --target-version 59 --proof build/native-59-59.json
```

Existing validated combinations are source 58/59/62 to target 58/59; runtime selection and full schema checks still apply. New combinations require independent evidence, not an edited allowlist. The release validation summary in docs/validation-v0.1.0.json states what was actually tested and its limits.

## Build the standalone helper

Build on a macOS Apple Silicon host:

```sh
python3 -m venv .build-venv
.build-venv/bin/python -m pip install -r tools/build-requirements.txt
.build-venv/bin/python tools/build.py
```

The release is generated in dist/ using PyInstaller onedir. Pinned build inputs improve repeatability; macOS signing and build metadata mean byte-for-byte reproducibility is not promised. Run the frozen helper, checksum verification and isolated integration tests before distributing it. Ad-hoc signing is not Developer ID signing or notarization.

Release source archives must include only tracked source, docs and synthetic fixtures. Publish SHA256SUMS for downloadable artifacts. Never attach private integration directories, snapshots, client databases or raw reports to releases/issues. Do not use real personal profiles as tests.
