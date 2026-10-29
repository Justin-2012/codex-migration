# Third-party software

The source license does not replace dependency licenses. The standalone package includes Python and its standard library, zstandard, PyYAML and PyInstaller runtime components. License notices provided by the build tools are retained in the bundled runtime and release licenses/ directory; the source distributions of those projects remain authoritative for their terms:

- Python: https://github.com/python/cpython/blob/main/LICENSE
- python-zstandard: https://github.com/indygreg/python-zstandard/blob/main/LICENSE
- Zstandard: https://github.com/facebook/zstd/blob/dev/LICENSE
- PyYAML: https://github.com/yaml/pyyaml/blob/main/LICENSE
- PyInstaller (including its bootloader exception): https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt

No OpenAI application binary, Codex CLI binary or personal account data is redistributed. Native validation uses the user's already installed, compatible client runtime. Full pinned build inputs are in tools/build-requirements.txt.

Native dependency notices are also included in [licenses/](licenses/README.md), including OpenSSL, liblzma, libmpdec and Zstandard.
