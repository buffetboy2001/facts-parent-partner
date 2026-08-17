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

For repository development, create a local `.env` file containing the required
authentication settings.

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
  since: monday # Download files uploaded after the most recent Monday.
classes:
  skip:
    - ST HALL-M/F 9th # Exact Class-column labels to exclude before navigation.
visible: true
```

Relative download paths are resolved from the directory containing
`config.yaml`. The directory is created when it is missing. Before opening the
portal, the application verifies it can write to that directory and exits with
an error if it cannot.

## Run

From the repository root on macOS, use uv to load the local `.env` file and
run the packaged command:

```bash
uv run --env-file .env facts-parent-partner --config config.yaml
```

From PowerShell, run:

```powershell
.\run.ps1
```

## Windows scheduled automation

Install the packaged command once on the target machine:

```powershell
uv tool install git+https://github.com/buffetboy2001/facts-parent-partner.git
uv tool update-shell
```

The installation provides `facts-parent-partner.exe`. Keep `config.yaml` in a
user-owned directory and invoke it explicitly:

```powershell
facts-parent-partner --config C:\Automation\facts-parent-partner\config.yaml
```

For a one-off run without a persistent tool installation, use:

```powershell
uvx --from git+https://github.com/buffetboy2001/facts-parent-partner.git facts-parent-partner --config C:\Automation\facts-parent-partner\config.yaml
```

For Task Scheduler, set the action to run [run.ps1](run.ps1) with
`-ConfigPath C:\Automation\facts-parent-partner\config.yaml`. Configure the
`FACTS_USERNAME`, `FACTS_PASSWORD`, and `FACTS_DISTRICT_CODE` authentication
values as environment variables for the scheduled user; do not put them in the
YAML file or pass them as command-line arguments.

### Task

The script visits every link in the Classes table, opens each class's
**Resources** tab, and saves its displayed PDF documents. A class with no
resources receives an empty `[class]_no_resources.txt` marker in the download
directory, without stopping the other classes. Resources are processed in the
portal's shown order. `downloads.since` accepts a weekday name and downloads
only files with a later displayed upload date. For example, on Sunday, 16 August
2026, `since: monday` uses 10 August 2026 as the cutoff and downloads files
uploaded on 11 August or later. Use `since: all` to ignore upload dates and
download every PDF found for each class. Set `visible: false` in `config.yaml`
to run without a browser window. Existing downloads are never overwritten.

Add class labels to `classes.skip` when a class is known never to have
resources. Skipped classes are not opened; matching ignores capitalization and
extra spaces.

---

~ Enjoy! ~
