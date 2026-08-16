"""Automate the FACTS Family Portal sign-in and sign-out flow.

Authentication settings must be supplied in the process environment. Run with
``uv run --env-file .env`` locally; this script never reads .env itself.
"""

from __future__ import annotations

import os
import re

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


FAMILY_PORTAL_URL = "https://sis.factsmgt.com/family-portal"


def click_if_present(page: Page, name: str, timeout: int = 5_000) -> bool:
    """Click an exact button/link if the current page presents it."""
    control = page.get_by_text(name, exact=True).first
    try:
        control.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        return False
    control.click()
    return True


def main() -> None:
    username = os.environ.get("FACTS_USERNAME")
    password = os.environ.get("FACTS_PASSWORD")
    district_code = os.environ.get("FACTS_DISTRICT_CODE")
    if not username or not password or not district_code:
        raise RuntimeError("Required authentication settings are unavailable.")

    # The recorded window is 1280 px wide. Set HEADLESS=true for CI.
    headless = os.environ.get("HEADLESS", "false").lower() == "true"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.set_default_timeout(15_000)

        try:
            # Enter through the same Family Portal URL as the site's login link.
            # FACTS first asks for a district code, then presents the account form.
            page.goto(FAMILY_PORTAL_URL, wait_until="domcontentloaded")
            district_input = page.locator("#rw-district-code")
            district_input.wait_for(state="visible")
            district_input.fill(district_code)

            # Do not use Playwright's value assertion here: its failure message
            # would include the configured district code in terminal output.
            if district_input.input_value() != district_code:
                raise RuntimeError("The district code could not be entered.")
            page.locator("#next").click()

            # FACTS has used both "Username" and "Username or Email" labels.
            username_input = page.get_by_label(
                re.compile(r"username|email", re.IGNORECASE)
            )
            username_input.wait_for(state="visible", timeout=30_000)
            username_input.fill(username)
            # The label "Password" also labels FACTS's show-password button,
            # so target the password input rather than using a label regex.
            password_input = page.locator("input[name='password']")
            password_input.wait_for(state="visible")
            password_input.fill(password)

            # Angular enables this button only after it validates both fields.
            # Wait for that state instead of racing the reactive form update.
            sign_in = page.locator("button[type='submit'][aria-label='sign in']")
            sign_in.wait_for(state="visible")
            page.wait_for_function(
                "button => !button.disabled",
                arg=sign_in.element_handle(),
                timeout=30_000,
            )
            sign_in.click()

            # The video chooses this option only when the MFA screen appears.
            click_if_present(page, "Maybe later", timeout=12_000)

            # FACTS redirects through Nelnet, then loads the SIS family portal.
            page.wait_for_url("**sis.factsmgt.com/**", timeout=30_000)
            page.get_by_text("School Home", exact=True).wait_for(
                state="visible", timeout=30_000
            )

            # Open the top-right initials avatar. It has no stable accessible
            # name, but the app-bar menu-trigger classes are stable in FACTS.
            profile_menu = page.locator(
                "button.mat-mdc-menu-trigger.app-bar-badge"
            )
            profile_menu.wait_for(state="visible")
            profile_menu.click()
            page.get_by_text("Log Out", exact=True).wait_for(state="visible")
            page.get_by_text("Log Out", exact=True).click()
            page.get_by_text("Session Logged Out", exact=True).wait_for(
                state="visible", timeout=20_000
            )
            print("Signed in, deferred MFA when offered, and logged out.")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
