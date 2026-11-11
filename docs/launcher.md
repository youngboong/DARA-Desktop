# START.cmd reference

What `START.cmd` installs, its switches and its exit codes. For everyday use the
[README](../README.md) is enough.

## What the first launch installs

Everything goes inside the DARA folder. Nothing is added to the system Python, PATH, registry or
user profile.

1. A pinned [uv](https://docs.astral.sh/uv/) release, SHA-256 verified, into `tools\uv\`.
2. CPython 3.12 into `tools\python\`.
3. The environment `.venv\` with this checkout in editable mode. Versions are pinned by
   `scripts\constraints-py312-win.txt`.
4. The BGMN refinement programs, downloaded only if `src\dara\bgmn\BGMNwin` lacks them. This is
   checked on every launch.

The first launch needs access to `github.com` and `pypi.org`. Windows proxy settings are honoured.

## Networks that inspect HTTPS

START builds a CA bundle from certifi plus the Windows certificate store
(`tools\certs\dara-ca-bundle.pem`) and passes it to the server through `SSL_CERT_FILE` and
`REQUESTS_CA_BUNDLE`. COD and Materials Project downloads then work behind an inspecting proxy
instead of failing with `CERTIFICATE_VERIFY_FAILED`. Set `DARA_CA_BUNDLE=off` to disable this.

## Switches

Switches go after `START.cmd`, for example `START.cmd -Reinstall`.

| Switch | Effect |
| --- | --- |
| `-Reinstall` | Recreate `.venv`. `tools\` and the download cache are kept, so this also works offline. |
| `-SetupOnly` | Install and verify, then exit without starting the server. |
| `-Offline` | Never use the network. Fail with a clear message if something is missing. |
| `-PythonExe <path>` | Expert option: use this interpreter as it is and skip setup (same as `DARA_PYTHON`). |
| `-NoBrowser` | Do not open a browser window. |
| `-SkipApiKeySetup` | Do not show the Materials Project key dialog. |
| `-StartupTimeoutSeconds <n>` | How long to wait for the server (default 180). |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Normal |
| 1 | Unexpected error, or another START is installing |
| 2 | No internet and no usable environment |
| 3 | BGMN missing and not downloadable |
| 4 | uv download or checksum failed |
| 5 | Python 3.12 not installable |
| 6 | Package installation failed |
| 7 | Import check failed |

## Logs

- Server start: `artifacts\launcher\server-*.log`
- Setup: `artifacts\launcher\setup-YYYYMMDD-HHMMSS.log` (the newest 10 are kept)

## Recovery

- Setup was interrupted: run START again. It resumes.
- The folder was moved: START rebuilds the environment. This works offline while `tools\uv-cache`
  is intact.
- Reset: delete `.venv` or run `START.cmd -Reinstall`. Delete `tools\` as well to download
  everything again.
