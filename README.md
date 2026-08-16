# FACTS Parent Partner

Automates FACTS/Nelnet website interaction.

## Setup

Install the locked project dependencies:

```bash
uv sync
```

If Playwright has not installed Chromium on this machine yet:

```bash
uv run playwright install chromium
```

Create a local `.env` file containing the required authentication settings. 

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

For a headless run:

```bash
HEADLESS=true uv run --env-file .env python main.py
```

---

~ Enjoy! ~