# FACTS Parent Partner

Automates the FACTS/Nelnet sign-in flow from the supplied screen recording:
sign in, defer MFA when the optional prompt appears, open the profile menu, and
sign out.

## Setup

Install the locked project dependencies:

```bash
uv sync
```

If Playwright has not installed Chromium on this machine yet:

```bash
uv run playwright install chromium
```

Create a local `.env` file containing the required authentication settings. The
district code is required because FACTS asks for it before showing the account
sign-in form:

```dotenv
FACTS_USERNAME=
FACTS_PASSWORD=
FACTS_DISTRICT_CODE=
```

`.env` is ignored by Git. Keep the values private and do not paste them into
issues, logs, or source files.

## Run

`main.py` is the primary entry point. Use uv to load the local environment file
and run it:

```bash
uv run --env-file .env python main.py
```

The script starts at `https://sis.factsmgt.com/family-portal`, enters the
district code, and then completes the sign-in flow. It opens a browser by
default and logs out when the recorded flow is complete. For a headless run,
such as in CI:

```bash
HEADLESS=true uv run --env-file .env python main.py
```

For CI or production automation, inject these variables from the platform's
secret manager instead of creating a `.env` file:

```bash
uv run python main.py
```

`test_script.py` remains as a small compatibility launcher for the former
command, but new usage should target `main.py`.
