"""Download PDF resources for a class in the FACTS Family Portal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Download,
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.sync_api import sync_playwright
import yaml


FAMILY_PORTAL_URL = "https://sis.factsmgt.com/family-portal"
FACTS_DOMAIN = "factsmgt.com"
PDF_PATTERN = re.compile(r"\.pdf(?:$|[?#])", re.IGNORECASE)
UPLOAD_DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
CONFIG_PATH = Path(__file__).with_name("config.yaml")
CLASS_LINK_SELECTOR = "tr a[href]"
CLASSES_RENDER_DELAY_MS = 5_000
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


@dataclass(frozen=True)
class AppConfig:
    """Non-sensitive settings loaded from ``config.yaml``."""

    download_dir: Path
    since: date | None
    skipped_classes: frozenset[str]
    visible: bool


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


def load_config(config_path: Path = CONFIG_PATH) -> AppConfig:
    """Load and validate the non-sensitive application configuration."""
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
    if not isinstance(downloads, dict):
        raise RuntimeError("Configuration field 'downloads' must be a mapping.")
    if not isinstance(classes, dict):
        raise RuntimeError("Configuration field 'classes' must be a mapping.")
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
    visible = data.get("visible", True)
    if not isinstance(visible, bool):
        raise RuntimeError("Configuration field 'visible' must be true or false.")
    return AppConfig(
        download_dir=writable_download_directory(download_location, config_path),
        since=most_recent_weekday(since.strip()),
        skipped_classes=frozenset(normalized_class_label(class_name) for class_name in skip),
        visible=visible,
    )


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


def open_classes(page: Page) -> None:
    """Navigate to Classes; the class-list wait is the readiness signal."""
    classes_link = page.locator("a[href*='/school/classes']").first
    try:
        classes_link.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError:
        # Keep a text fallback for portal versions without an href.
        page.get_by_text("Classes", exact=True).first.click()
    else:
        classes_link.click()
    page.wait_for_url("**/school/classes**", timeout=30_000)
    # FACTS often updates the URL before its class-list application finishes
    # initializing. Give that client-side transition time to complete before
    # polling for class links.
    page.wait_for_timeout(CLASSES_RENDER_DELAY_MS)


def class_link_container(page: Page) -> tuple[Page | Frame, Locator]:
    """Locate class links in the page or an embedded FACTS school frame."""
    deadline = time.monotonic() + 30
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
            print(f"Ignored {link.inner_text().strip()}: untrusted class link destination.")
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


def download_pdfs(page: Page, settings: Settings) -> list[Path]:
    """Download PDFs newer than the cutoff, or every PDF in ``all`` mode."""
    saved_files: list[Path] = []
    for link in pdf_resource_links(page):
        upload_date = resource_upload_date(link)
        if settings.since is not None and (
            upload_date is None or upload_date <= settings.since
        ):
            continue
        with page.expect_download(timeout=30_000) as download_info:
            download_control(link).click()
        download: Download = download_info.value
        destination = unique_destination(settings.download_dir, download.suggested_filename)
        download.save_as(destination)
        saved_files.append(destination)
    return saved_files


def download_all_class_resources(page: Page, settings: Settings) -> tuple[list[Path], list[Path]]:
    """Visit every listed class and collect PDFs or no-resource markers."""
    open_classes(page)
    classes = enrolled_class_urls(page)
    saved_files: list[Path] = []
    no_resource_markers: list[Path] = []
    for class_label, class_url in classes:
        if normalized_class_label(class_label) in settings.skipped_classes:
            print(f"Skipped {class_label}: configured in classes.skip.")
            continue
        try:
            has_documents = open_class_resources(page, class_url)
            if not has_documents:
                no_resource_markers.append(
                    no_resources_marker(settings.download_dir, class_label)
                )
                continue
            saved_files.extend(download_pdfs(page, settings))
        except (PlaywrightTimeoutError, RuntimeError) as error:
            print(f"Skipped {class_label}: resources could not be loaded ({error}).")
    return saved_files, no_resource_markers


def main() -> None:
    """Sign in and save PDF resources for every class on the Classes page."""
    settings = load_settings(load_config())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720}, accept_downloads=True
        )
        page = context.new_page()
        page.set_default_timeout(15_000)
        try:
            sign_in(page, settings)
            downloaded_files, no_resource_markers = download_all_class_resources(
                page, settings
            )
            cutoff_description = (
                "all upload dates"
                if settings.since is None
                else f"upload dates after {settings.since:%d %B %Y}"
            )
            print(
                f"Downloaded {len(downloaded_files)} PDF resource(s) for "
                f"{cutoff_description}; created {len(no_resource_markers)} "
                "no-resources marker file(s)."
            )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
