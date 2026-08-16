# FACTS Parent Partner

Automates FACTS/Nelnet website interaction.

## One-Time Setup

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

`.env` is ignored by Git. Keep the values private and do not paste them into issues, logs, or source files.

Configure downloads and other non-secret preferences in `config.yaml`:

```yaml
downloads:
  location: ./downloads
  max_recent: 0 # Download every displayed PDF for each class.
headless: false
```

Relative download paths are resolved from the directory containing
`config.yaml`. The directory is created when it is missing. Before opening the
portal, the application verifies it can write to that directory and exits with
an error if it cannot.

## Run

`main.py` is the primary entry point. Use uv to load the local environment file and run it:

```bash
uv run --env-file .env python main.py
```

### Task

The script visits every link in the Classes table, opens each class's
**Resources** tab, and saves its displayed PDF documents. A class with no
resources receives an empty `[class]_no_resources.txt` marker in the download
directory, without stopping the other classes. Resources are processed in the
portal's shown order. Set `downloads.max_recent` to a positive number to limit
downloads per class, or `0` for all PDFs. Set `headless: true` in `config.yaml`
to run without a browser window. Existing downloads are never overwritten.

---

~ Enjoy! ~
