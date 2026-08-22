"""Download PDF resources for each class in the FACTS Family Portal."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Sequence
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Download,
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.sync_api import sync_playwright
from loguru import logger
import yaml


FAMILY_PORTAL_URL = "https://sis.factsmgt.com/family-portal"
FACTS_DOMAIN = "factsmgt.com"
PDF_PATTERN = re.compile(r"\.pdf(?:$|[?#])", re.IGNORECASE)
UPLOAD_DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
DEFAULT_CONFIG_FILENAME = "config.yaml"
CLASS_LINK_SELECTOR = "tr a[href]"
CLASSES_RENDER_TIMEOUT_SECONDS = 30
LOG_LEVELS = frozenset(
    {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
)
WEEKDAY_NAMES = {
    name.lower(): number
    for number, name in enumerate(
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    )
}


@dataclass(frozen=True)
class Settings:
    """Runtime settings for one all-class resource download run."""

    username: str
    password: str
    district_code: str
    download_dir: Path
    since: date | None
    skipped_classes: frozenset[str]
    headless: bool
    dry_run: bool


@dataclass(frozen=True)
class AppConfig:
    """Non-sensitive settings loaded from ``config.yaml``."""

    download_dir: Path
    since: date | None
    skipped_classes: frozenset[str]
    visible: bool
    dry_run: bool
    log_level: str
    log_filename: Path
    log_mode: str
    yaml_settings: dict[str, object]


@dataclass(frozen=True)
class ResourceRunResult:
    """Files written and output actions identified during a resource run."""

    downloaded_files: list[Path]
    no_resource_markers: list[Path]
    pending_downloads: int
    pending_no_resource_markers: int


def required_environment(name: str) -> str:
    """Return a required environment value without exposing it in errors."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required setting {name} is unavailable.")
    return value


def writable_download_directory(location: str, config_path: Path) -> Path:
    """Create and verify the configured download directory before using it."""
    configured_path = Path(location).expanduser()
    directory = configured_path if configured_path.is_absolute() else config_path.parent / configured_path
    if directory.exists() and not directory.is_dir():
        raise RuntimeError(f"Configured download location is not a directory: {directory}")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"Cannot create download directory: {directory}") from error
    if not directory.is_dir():
        raise RuntimeError(f"Configured download location is not a directory: {directory}")

    # ``os.access`` can be misleading with ACLs, so verify permissions by
    # creating a temporary file in the target directory instead.
    try:
        with tempfile.NamedTemporaryFile(dir=directory):
            pass
    except OSError as error:
        raise RuntimeError(f"Download directory is not writable: {directory}") from error
    return directory.resolve()


def dated_download_directory(base_directory: Path, today: date | None = None) -> Path:
    """Create and verify the daily download directory under the configured root."""
    directory = base_directory / (today or date.today()).isoformat()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"Cannot create daily download directory: {directory}") from error
    try:
        with tempfile.NamedTemporaryFile(dir=directory):
            pass
    except OSError as error:
        raise RuntimeError(f"Daily download directory is not writable: {directory}") from error
    return directory.resolve()


def writable_log_file(location: str, config_path: Path) -> Path:
    """Create and verify the configured log file before Loguru uses it."""
    configured_path = Path(location).expanduser()
    log_file = configured_path if configured_path.is_absolute() else config_path.parent / configured_path
    if log_file.exists() and log_file.is_dir():
        raise RuntimeError(f"Configured log filename is a directory: {log_file}")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8"):
            pass
    except OSError as error:
        raise RuntimeError(f"Log file is not writable: {log_file}") from error
    return log_file.resolve()


def most_recent_weekday(weekday_name: str, today: date | None = None) -> date | None:
    """Return a weekday cutoff, or ``None`` when ``all`` is configured."""
    if weekday_name.lower() == "all":
        return None
    weekday = WEEKDAY_NAMES.get(weekday_name.lower())
    if weekday is None:
        valid_days = ", ".join(WEEKDAY_NAMES)
        raise RuntimeError(f"Configuration field 'downloads.since' must be a weekday: {valid_days}.")
    calendar_day = today or date.today()
    return calendar_day - timedelta(days=(calendar_day.weekday() - weekday) % 7)


def normalized_class_label(label: str) -> str:
    """Normalize a displayed class name for skip-list comparisons."""
    return " ".join(label.split()).casefold()


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load and validate the non-sensitive application configuration."""
    config_path = (config_path or Path.cwd() / DEFAULT_CONFIG_FILENAME).expanduser()
    try:
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except FileNotFoundError as error:
        raise RuntimeError(f"Configuration file was not found: {config_path}") from error
    except yaml.YAMLError as error:
        raise RuntimeError(f"Configuration file is invalid YAML: {config_path}") from error

    if not isinstance(data, dict):
        raise RuntimeError("Configuration must contain a YAML mapping.")
    downloads = data.get("downloads")
    classes = data.get("classes", {})
    logging_config = data.get("logging")
    if not isinstance(downloads, dict):
        raise RuntimeError("Configuration field 'downloads' must be a mapping.")
    if not isinstance(classes, dict):
        raise RuntimeError("Configuration field 'classes' must be a mapping.")
    if not isinstance(logging_config, dict):
        raise RuntimeError("Configuration field 'logging' must be a mapping.")
    download_location = downloads.get("location")
    since = downloads.get("since")
    if not isinstance(download_location, str) or not download_location.strip():
        raise RuntimeError("Configuration field 'downloads.location' must be a non-empty string.")
    if not isinstance(since, str) or not since.strip():
        raise RuntimeError("Configuration field 'downloads.since' must be a weekday name.")
    skip = classes.get("skip", [])
    if not isinstance(skip, list) or any(
        not isinstance(class_name, str) or not class_name.strip() for class_name in skip
    ):
        raise RuntimeError("Configuration field 'classes.skip' must be a list of class names.")
    log_level = logging_config.get("level")
    log_filename = logging_config.get("filename")
    log_mode = logging_config.get("mode", "append")
    if not isinstance(log_level, str) or log_level.upper() not in LOG_LEVELS:
        raise RuntimeError(
            "Configuration field 'logging.level' must be TRACE, DEBUG, INFO, "
            "SUCCESS, WARNING, ERROR, or CRITICAL."
        )
    if not isinstance(log_filename, str) or not log_filename.strip():
        raise RuntimeError("Configuration field 'logging.filename' must be a non-empty string.")
    if not isinstance(log_mode, str) or log_mode.lower() not in {"append", "replace"}:
        raise RuntimeError("Configuration field 'logging.mode' must be append or replace.")
    visible = data.get("visible", True)
    if not isinstance(visible, bool):
        raise RuntimeError("Configuration field 'visible' must be true or false.")
    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise RuntimeError("Configuration field 'dry_run' must be true or false.")
    download_root = writable_download_directory(download_location, config_path)
    return AppConfig(
        download_dir=dated_download_directory(download_root),
        since=most_recent_weekday(since.strip()),
        skipped_classes=frozenset(normalized_class_label(class_name) for class_name in skip),
        visible=visible,
        dry_run=dry_run,
        log_level=log_level.upper(),
        log_filename=writable_log_file(log_filename, config_path),
        log_mode=log_mode.lower(),
        yaml_settings={
            "downloads.location": download_location,
            "downloads.since": since,
            "classes.skip": skip,
            "visible": visible,
            "dry_run": dry_run,
            "logging.level": log_level,
            "logging.filename": log_filename,
            "logging.mode": log_mode,
        },
    )


def configure_logging(config: AppConfig) -> None:
    """Configure Loguru to write configured-level events to console and file."""
    minimum_level = logger.level(config.log_level).no

    def configured_level_or_run_start(record: dict[str, object]) -> bool:
        return bool(
            record["extra"].get("run_start")
            or record["level"].no >= minimum_level
        )

    logger.remove()
    logger.add(sys.stderr, level="TRACE", filter=configured_level_or_run_start)
    file_mode = "a" if config.log_mode == "append" else "w"
    logger.add(
        config.log_filename,
        level="TRACE",
        filter=configured_level_or_run_start,
        encoding="utf-8",
        mode=file_mode,
    )


def log_run_started() -> None:
    """Emit the run marker even when the configured threshold is above INFO."""
    logger.bind(run_start=True).info("FACTS Parent Partner run started.")


def log_configuration(config: AppConfig) -> None:
    """Log the validated, non-secret YAML settings at the start of a run."""
    for name, value in config.yaml_settings.items():
        logger.debug("YAML setting {}: {}", name, value)


def load_settings(config: AppConfig) -> Settings:
    """Build settings from environment variables and validate simple options."""
    return Settings(
        username=required_environment("FACTS_USERNAME"),
        password=required_environment("FACTS_PASSWORD"),
        district_code=required_environment("FACTS_DISTRICT_CODE"),
        download_dir=config.download_dir,
        since=config.since,
        skipped_classes=config.skipped_classes,
        headless=not config.visible,
        dry_run=config.dry_run,
    )


def click_if_present(page: Page, name: str, timeout: int = 5_000) -> bool:
    """Click an exact button/link if the current page presents it."""
    control = page.get_by_text(name, exact=True).first
    try:
        control.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        return False
    control.click()
    return True


def sign_in(page: Page, settings: Settings) -> None:
    """Authenticate and wait for the SIS Family Portal home screen."""
    page.goto(FAMILY_PORTAL_URL, wait_until="domcontentloaded")
    district_input = page.locator("#rw-district-code")
    district_input.wait_for(state="visible")
    district_input.fill(settings.district_code)
    if district_input.input_value() != settings.district_code:
        raise RuntimeError("The district code could not be entered.")
    page.locator("#next").click()

    username_input = page.get_by_label(re.compile(r"username|email", re.IGNORECASE))
    username_input.wait_for(state="visible", timeout=30_000)
    username_input.fill(settings.username)
    password_input = page.locator("input[name='password']")
    password_input.wait_for(state="visible")
    password_input.fill(settings.password)

    sign_in_button = page.locator("button[type='submit'][aria-label='sign in']")
    sign_in_button.wait_for(state="visible")
    page.wait_for_function(
        "button => !button.disabled",
        arg=sign_in_button.element_handle(),
        timeout=30_000,
    )
    sign_in_button.click()
    click_if_present(page, "Maybe later", timeout=12_000)
    page.wait_for_url("**sis.factsmgt.com/**", timeout=30_000)
    page.get_by_text("School Home", exact=True).wait_for(state="visible", timeout=30_000)


def click_classes_after_portal_settles(page: Page) -> None:
    """Use FACTS's in-app route only after its home application becomes idle."""
    classes_link = page.locator("a[href*='/school/classes']").first
    try:
        classes_link.wait_for(state="visible", timeout=30_000)
    except PlaywrightTimeoutError:
        # Keep a text fallback for portal versions without an href.
        classes_link = page.get_by_text("Classes", exact=True).first
        classes_link.wait_for(state="visible", timeout=30_000)

    # FACTS can display its navigation before the surrounding application has
    # finished initializing. Preserve its in-memory routing context by using
    # the link, but do not click while startup requests are still in flight.
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        logger.warning("FACTS remained network-active before Classes navigation.")
    classes_link.click()
    page.wait_for_url("**/school/classes**", timeout=30_000)


def open_classes(page: Page) -> None:
    """Open Classes and repeat the in-app transition once after a bad render."""
    for attempt in range(2):
        if attempt:
            logger.warning(
                "Classes did not render cleanly; returning to School Home and retrying once."
            )
            page.go_back(wait_until="domcontentloaded")
            page.get_by_text("School Home", exact=True).first.wait_for(
                state="visible", timeout=30_000
            )

        click_classes_after_portal_settles(page)
        try:
            class_link_container(page, timeout=CLASSES_RENDER_TIMEOUT_SECONDS)
            return
        except RuntimeError:
            if attempt:
                raise


def class_link_container(
    page: Page, timeout: float = CLASSES_RENDER_TIMEOUT_SECONDS
) -> tuple[Page | Frame, Locator]:
    """Locate class links in the page or an embedded FACTS school frame."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for container in [page, *page.frames]:
            # FACTS currently renders a conventional table. Do not require
            # ``tbody``: some portal variants omit it while retaining rows.
            class_links = container.locator(CLASS_LINK_SELECTOR)
            if class_links.count() and class_links.first.is_visible():
                return container, class_links
        page.wait_for_timeout(500)
    raise RuntimeError(
        "FACTS did not render any class links in the Classes page or its "
        "embedded school content."
    )


def trusted_class_url(base_url: str, href: str) -> str | None:
    """Return a FACTS-owned class URL, rejecting unexpected destinations."""
    url = urljoin(base_url, href)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not (host == FACTS_DOMAIN or host.endswith(f".{FACTS_DOMAIN}"))
        or not (parsed.path.startswith("/family-portal/") or parsed.path.startswith("/pwr/"))
    ):
        return None
    return url


def enrolled_class_urls(page: Page) -> list[tuple[str, str]]:
    """Collect the Class-column links from the loaded Classes table."""
    container, class_links = class_link_container(page)

    classes: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for index in range(class_links.count()):
        link = class_links.nth(index)
        href = link.get_attribute("href")
        if not href:
            continue
        url = trusted_class_url(container.url, href)
        if url is None:
            logger.error("Ignored {}: untrusted class link destination.", link.inner_text().strip())
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        classes.append((link.inner_text().strip(), url))
    return classes


def open_class_resources(page: Page, class_url: str) -> bool:
    """Open one class and report whether its Resources tab has documents."""
    page.goto(class_url, wait_until="domcontentloaded")

    # The sidebar also contains a "Resources" entry, so use the final match:
    # the class tab appears after the sidebar in the document order.
    resources_tab = page.get_by_text("Resources", exact=True).last
    resources_tab.wait_for(state="visible", timeout=30_000)
    resources_tab.click()
    return wait_for_documents_state(page)


def wait_for_documents_state(page: Page) -> bool:
    """Wait for either the document list or FACTS's empty-resources message.

    The Documents heading includes a Material icon in its accessible text, so
    an exact ``Documents`` locator cannot identify this state reliably.
    """
    page.wait_for_function(
        """() => {
            const text = document.body.innerText;
            return text.includes('Upload Order') || text.includes('No documents found.');
        }""",
        timeout=30_000,
    )
    return "No documents found." not in page.locator("body").inner_text()


def is_pdf_resource(text: str, href: str | None) -> bool:
    """Identify PDF resources from either their displayed name or URL."""
    return bool(PDF_PATTERN.search(text.strip()) or (href and PDF_PATTERN.search(href)))


def pdf_resource_links(page: Page) -> list[Locator]:
    """Return visible PDF links in the portal's displayed order."""
    links = page.locator("a")
    pdf_links: list[Locator] = []
    for index in range(links.count()):
        link = links.nth(index)
        if link.is_visible() and is_pdf_resource(link.inner_text(), link.get_attribute("href")):
            pdf_links.append(link)
    return pdf_links


def resource_upload_date(link: Locator) -> date | None:
    """Read the upload date displayed alongside a resource in its table row."""
    row_text = link.locator("xpath=ancestor::tr[1]").inner_text()
    match = UPLOAD_DATE_PATTERN.search(row_text)
    if not match:
        return None
    return datetime.strptime(match.group(), "%m/%d/%Y").date()


def download_control(link: Locator) -> Locator:
    """Use a resource row's download icon when FACTS provides one.

    The recording uses this icon, while some portal versions download directly
    from the resource name. Supporting both keeps the automation compatible.
    """
    row = link.locator("xpath=ancestor::tr[1]")
    controls = row.locator(
        "[aria-label*='download' i], [title*='download' i], "
        "[mattooltip*='download' i], .download"
    )
    return controls.first if controls.count() else link


def unique_destination(directory: Path, filename: str) -> Path:
    """Return a unique, containment-checked destination for a server filename."""
    # Suggested filenames come from the remote site. Normalize both separator
    # styles before keeping only the basename, including on non-Windows hosts.
    safe_filename = Path(filename.replace("\\", "/")).name
    if safe_filename in {"", ".", ".."}:
        raise RuntimeError("FACTS provided an invalid download filename.")

    resolved_directory = directory.resolve()
    destination = (resolved_directory / safe_filename).resolve()
    if destination.parent != resolved_directory:
        raise RuntimeError("FACTS provided a download filename outside the configured directory.")
    stem, suffix = destination.stem, destination.suffix
    counter = 2
    while destination.exists():
        destination = resolved_directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return destination


def no_resources_marker(directory: Path, class_label: str) -> Path:
    """Create the requested empty marker file for a class with no documents."""
    safe_class_label = re.sub(r"[^A-Za-z0-9._-]+", "_", class_label).strip("._")
    filename = f"{safe_class_label or 'class'}_no_resources.txt"
    marker = directory / filename
    marker.write_text("", encoding="utf-8")
    return marker


def download_pdfs(page: Page, settings: Settings) -> tuple[list[Path], int]:
    """Download PDFs newer than the cutoff, or every PDF in ``all`` mode."""
    saved_files: list[Path] = []
    pending_downloads = 0
    for link in pdf_resource_links(page):
        upload_date = resource_upload_date(link)
        if settings.since is not None and (
            upload_date is None or upload_date <= settings.since
        ):
            continue
        if settings.dry_run:
            logger.debug("Dry run: would download {}.", link.inner_text().strip())
            pending_downloads += 1
            continue
        with page.expect_download(timeout=30_000) as download_info:
            download_control(link).click()
        download: Download = download_info.value
        destination = unique_destination(settings.download_dir, download.suggested_filename)
        download.save_as(destination)
        saved_files.append(destination)
        logger.trace("Downloaded PDF: {}", destination.name)
    return saved_files, pending_downloads


def download_all_class_resources(page: Page, settings: Settings) -> ResourceRunResult:
    """Visit every listed class and collect PDFs or no-resource markers."""
    open_classes(page)
    classes = enrolled_class_urls(page)
    saved_files: list[Path] = []
    no_resource_markers: list[Path] = []
    pending_downloads = 0
    pending_no_resource_markers = 0
    for class_label, class_url in classes:
        if normalized_class_label(class_label) in settings.skipped_classes:
            logger.debug("Skipped {}: configured in classes.skip.", class_label)
            continue
        try:
            has_documents = open_class_resources(page, class_url)
            if not has_documents:
                if settings.dry_run:
                    logger.debug(
                        "Dry run: would create a no-resources marker for {}.", class_label
                    )
                    pending_no_resource_markers += 1
                else:
                    no_resource_markers.append(
                        no_resources_marker(settings.download_dir, class_label)
                    )
                continue
            class_files, class_pending_downloads = download_pdfs(page, settings)
            saved_files.extend(class_files)
            pending_downloads += class_pending_downloads
        except (PlaywrightTimeoutError, RuntimeError) as error:
            logger.error("Skipped {}: resources could not be loaded ({}).", class_label, error)
    return ResourceRunResult(
        downloaded_files=saved_files,
        no_resource_markers=no_resource_markers,
        pending_downloads=pending_downloads,
        pending_no_resource_markers=pending_no_resource_markers,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI options without placing secrets in command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download recent FACTS class resources."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to the non-secret YAML configuration file (default: ./config.yaml).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Sign in and save PDF resources for every class on the Classes page."""
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception:
        # Configuration can fail before its file sink is usable, so this is
        # emitted through Loguru's default stderr sink for Task Scheduler.
        logger.exception("Unable to load application configuration.")
        raise
    configure_logging(config)
    log_run_started()
    log_configuration(config)
    try:
        settings = load_settings(config)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720}, accept_downloads=True
            )
            page = context.new_page()
            page.set_default_timeout(15_000)
            try:
                sign_in(page, settings)
                result = download_all_class_resources(page, settings)
                cutoff_description = (
                    "all upload dates"
                    if settings.since is None
                    else f"upload dates after {settings.since:%d %B %Y}"
                )
                if settings.dry_run:
                    logger.debug(
                        "Dry run: would download {} PDF resource(s) for {}; would create "
                        "{} no-resources marker file(s).",
                        result.pending_downloads,
                        cutoff_description,
                        result.pending_no_resource_markers,
                    )
                else:
                    logger.debug(
                        "Downloaded {} PDF resource(s) for {}; created {} no-resources "
                        "marker file(s).",
                        len(result.downloaded_files),
                        cutoff_description,
                        len(result.no_resource_markers),
                    )
            finally:
                context.close()
                browser.close()
    except Exception:
        logger.exception("FACTS resource download run failed.")
        raise


if __name__ == "__main__":
    main()
