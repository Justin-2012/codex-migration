# Native dependency notices

These notices accompany the native libraries included by the macOS build. The installed dependency versions used for v0.1.0 are OpenSSL 3.6.3, Zstandard 1.5.7, liblzma (XZ Utils) 5.8.3 and libmpdec 4.0.1. Source notice copies came from those local Homebrew distributions. Future builds must update the notices when changing dependencies.

SQLite is dedicated to the public domain: https://sqlite.org/copyright.html . The SQLite library is included; no SQLite client application is redistributed.

Python, python-zstandard, PyYAML and PyInstaller notices are copied from the build environment into the standalone release by tools/build.py. No OpenAI client binary is bundled.
