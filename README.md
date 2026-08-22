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
dry_run: false # Inspect the portal without writing output files when true.
logging:
  level: DEBUG
  filename: ./facts-parent-partner.log
  mode: append # Use replace to keep only the most recent run's records.
```

Relative download paths are resolved from the directory containing
`config.yaml`. The directory is created when it is missing. Before opening the
portal, the application verifies it can write to that directory and exits with
an error if it cannot. Each run saves into a dated `YYYY-MM-DD` subfolder of
`downloads.location` (for example, `downloads/2026-08-16`).

### Download date filter (`downloads.since`)

`downloads.since` controls which resources are eligible for download. It
accepts `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday`,
`sunday`, or `all` (capitalization does not matter).

For a weekday value, the application finds the most recent occurrence of that
weekday, including today, and uses it as an exclusive cutoff. Only resources
whose displayed upload date is later than the cutoff are downloaded. For
example, if the application runs on Sunday, 16 August 2026 with
`since: monday`, the cutoff is Monday, 10 August, so resources dated 11 August
or later are eligible. A resource dated on the cutoff day is not eligible.

Set `since: all` to disable the date filter and consider every resource.

### Logging (`logging.level` and `logging.mode`)

The `logging` settings apply to both console output and the configured log
file. `logging.filename` may be an absolute path or a path relative to the
directory containing `config.yaml`. Run-stopping errors include a traceback in
the log file.

`logging.level` sets the minimum severity that is recorded. It accepts the
following values (capitalization does not matter), ordered from most to least
verbose:

- `TRACE`: record all available diagnostic details.
- `DEBUG`: record debugging details and all higher-severity messages.
- `INFO`: record normal progress messages and all higher-severity messages.
- `SUCCESS`: record successful-operation messages and all higher-severity messages.
- `WARNING`: record potential problems and all higher-severity messages.
- `ERROR`: record failures and critical messages only.
- `CRITICAL`: record only critical failures.

`logging.mode` controls what happens to records already in the log file:

- `append`: preserve existing records and add the new run to the end of the
  file. This is the default when `mode` is omitted.
- `replace`: truncate the log file when a run starts. When that run finishes,
  the file contains records from that run only.

Set `dry_run: true` to exercise the complete portal navigation and resource
inspection flow without downloading PDFs or creating no-resource markers.

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
portal's shown order. Set `visible: false` in `config.yaml` to run without a
browser window. Set `dry_run: true` to inspect what would be downloaded without
writing output files. Existing downloads are never overwritten.

Add class labels to `classes.skip` when a class is known never to have
resources. Skipped classes are not opened; matching ignores capitalization and
extra spaces.

---

~ Enjoy! ~
