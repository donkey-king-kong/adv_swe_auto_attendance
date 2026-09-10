from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"
NTU_LEARN_URL = "https://ntulearn.ntu.edu.sg/"
NTU_COURSES_URL = "https://ntulearn.ntu.edu.sg/ultra/course"
DEFAULT_MODULE_NAME = "26S1-SC3040-ADVANCED SOFTWARE ENGINEERING"
DEFAULT_ATTENDANCE_FOLDER_NAME = "Tutorial Attendance"
DEFAULT_ATTENDANCE_RECORDS_NAME = "Tutorial attendance records"
DEFAULT_ATTENDANCE_TIMEZONE = "Asia/Singapore"
DEFAULT_ATTENDANCE_DAY = "thursday"
DEFAULT_ATTENDANCE_START = "10:30"
DEFAULT_ATTENDANCE_END = "11:30"
TUTORIAL_ATTENDANCE_PATTERN = re.compile(r"^Tutorial\s+\d+\s+attendance$", re.I)
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
COURSES_NAV_SELECTORS = [
    'a[data-analytics-id="base.nav.navigation.courses"]',
    'a[href="https://ntulearn.ntu.edu.sg/ultra/course"]',
    'a[href*="/ultra/course"]',
    'a:has-text("Courses")',
]
ATTENDANCE_FOLDER_SELECTORS = [
    'button[aria-label="Open Folder, Tutorial Attendance"]',
    'button:has-text("Tutorial Attendance")',
    '[data-analytics-id="content.item.folder.toggleFolder.button"]:has-text("Tutorial Attendance")',
]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name}. Add it to {ENV_PATH}.")
    return value


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer.")


def log(message: str = "") -> None:
    print(message, flush=True)


def is_real_url(url: str | None) -> bool:
    return bool(url and url.startswith(("http://", "https://")))


def parse_weekday(value: str) -> int:
    weekday = WEEKDAYS.get(value.strip().lower())
    if weekday is None:
        valid_days = ", ".join(WEEKDAYS)
        raise RuntimeError(f"ATTENDANCE_DAY must be one of: {valid_days}.")
    return weekday


def parse_hhmm(name: str, value: str) -> clock_time:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError:
        raise RuntimeError(f"{name} must use HH:MM format, e.g. 10:30.")
    return parsed.time()


def get_attendance_window(now: datetime) -> tuple[datetime, datetime]:
    attendance_day = parse_weekday(os.getenv("ATTENDANCE_DAY", DEFAULT_ATTENDANCE_DAY))
    start_time = parse_hhmm(
        "ATTENDANCE_START",
        os.getenv("ATTENDANCE_START", DEFAULT_ATTENDANCE_START),
    )
    end_time = parse_hhmm(
        "ATTENDANCE_END",
        os.getenv("ATTENDANCE_END", DEFAULT_ATTENDANCE_END),
    )

    days_until_window = (attendance_day - now.weekday()) % 7
    window_date = now.date() + timedelta(days=days_until_window)
    window_start = datetime.combine(window_date, start_time, tzinfo=now.tzinfo)
    window_end = datetime.combine(window_date, end_time, tzinfo=now.tzinfo)

    if window_end <= window_start:
        raise RuntimeError("ATTENDANCE_END must be after ATTENDANCE_START.")
    if now > window_end:
        window_start += timedelta(days=7)
        window_end += timedelta(days=7)

    return window_start, window_end


def format_countdown(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def wait_until_attendance_window() -> datetime:
    timezone_name = os.getenv("ATTENDANCE_TIMEZONE", DEFAULT_ATTENDANCE_TIMEZONE)
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    window_start, window_end = get_attendance_window(now)

    log(
        "Attendance window: "
        f"{window_start.strftime('%A %Y-%m-%d %H:%M')} to "
        f"{window_end.strftime('%H:%M')} {timezone_name}"
    )

    if now < window_start:
        wait_seconds = int((window_start - now).total_seconds())
        log(f"Outside attendance window. Waiting {wait_seconds} seconds until it opens...")
        while wait_seconds > 0:
            log(
                "Countdown to attendance start: "
                f"{format_countdown(wait_seconds)} "
                f"(opens at {window_start.strftime('%H:%M:%S')} {timezone_name})"
            )
            sleep_seconds = min(30 if wait_seconds > 60 else 10, wait_seconds)
            time.sleep(sleep_seconds)
            now = datetime.now(timezone)
            wait_seconds = int((window_start - now).total_seconds())
        log("Attendance window is open. Starting checks now.")
    elif now <= window_end:
        log("Inside attendance window. Starting checks now.")

    return window_end


def attendance_window_is_open(window_end: datetime) -> bool:
    return datetime.now(window_end.tzinfo) <= window_end


def wait_after_login(page: Page) -> None:
    wait_seconds = env_int("WAIT_AFTER_LOGIN_SECONDS", 10)
    if wait_seconds <= 0:
        return
    log(f"Waiting {wait_seconds} seconds after login/page load...")
    page.wait_for_timeout(wait_seconds * 1000)


def first_visible(page: Page, selectors: list[str], timeout_ms: int = 1500):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except PlaywrightTimeoutError:
            continue
    return None


def click_if_visible(page: Page, selectors: list[str], timeout_ms: int = 1500) -> bool:
    locator = first_visible(page, selectors, timeout_ms)
    if locator is None:
        return False
    locator.click()
    return True


def fill_if_visible(
    page: Page,
    selectors: list[str],
    value: str,
    timeout_ms: int = 3000,
) -> bool:
    locator = first_visible(page, selectors, timeout_ms)
    if locator is None:
        return False
    locator.fill(value)
    return True


def maybe_submit_username(page: Page, username: str) -> None:
    if fill_if_visible(
        page,
        [
            'input[type="email"]',
            'input[name="loginfmt"]',
            'input[name="UserName"]',
            'input[name="username"]',
            'input[id*="user" i]',
            'input[placeholder*="email" i]',
            'input[placeholder*="username" i]',
        ],
        username,
    ):
        click_if_visible(
            page,
            [
                'input[type="submit"]',
                'button[type="submit"]',
                'button:has-text("Next")',
                'input[value="Next"]',
                'button:has-text("Continue")',
                'input[value="Continue"]',
            ],
        )


def maybe_submit_password(page: Page, password: str) -> None:
    if fill_if_visible(
        page,
        [
            'input[type="password"]',
            'input[name="passwd"]',
            'input[name="Password"]',
        ],
        password,
    ):
        click_if_visible(
            page,
            [
                'input[type="submit"]',
                'button[type="submit"]',
                'button:has-text("Sign in")',
                'input[value="Sign in"]',
                'button:has-text("Login")',
                'input[value="Login"]',
            ],
        )


def maybe_confirm_stay_signed_in(page: Page) -> None:
    click_if_visible(
        page,
        [
            'input[value="Yes"]',
            'button:has-text("Yes")',
            'input[value="No"]',
            'button:has-text("No")',
        ],
        timeout_ms=3000,
    )


def click_courses(page: Page) -> None:
    log("Clicking Courses...")

    try:
        page.get_by_role("link", name="Courses").click(timeout=10000)
    except PlaywrightError:
        courses_link = first_visible(page, COURSES_NAV_SELECTORS, timeout_ms=10000)
        if courses_link is not None:
            try:
                courses_link.scroll_into_view_if_needed(timeout=5000)
                courses_link.click(timeout=10000)
            except PlaywrightError:
                pass

    try:
        page.wait_for_url("**/ultra/course**", timeout=10000)
    except PlaywrightTimeoutError:
        log("Courses click did not navigate. Going directly to Courses URL...")
        page.goto(NTU_COURSES_URL, wait_until="domcontentloaded", timeout=60000)

    page.wait_for_load_state("domcontentloaded", timeout=30000)
    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")


def click_module(page: Page, module_name: str, module_url_override: str | None) -> str:
    log(f"Clicking module: {module_name}")

    if is_real_url(module_url_override):
        log(f"Using configured module URL: {module_url_override}")
        page.goto(module_url_override, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        log(f"Current page title: {page.title()!r}")
        log(f"Current URL: {page.url}")
        return module_url_override

    module_url = None
    try:
        module_link = page.get_by_role("link", name=module_name).first
        module_link.wait_for(state="visible", timeout=10000)
        module_url = module_link.get_attribute("href")
        module_link.click(timeout=10000)
    except PlaywrightTimeoutError:
        module_link = page.locator(
            f'a[analytics-id^="base.courses.courseCard.courseLink"]:has-text("{module_name}")'
        ).first
        try:
            module_link.wait_for(state="visible", timeout=10000)
            module_url = module_link.get_attribute("href")
            module_link.click()
        except PlaywrightTimeoutError:
            raise RuntimeError(f"Could not find module card/link: {module_name}")

    page.wait_for_load_state("domcontentloaded", timeout=30000)
    try:
        page.wait_for_url("**/ultra/courses/**", timeout=10000)
    except PlaywrightTimeoutError:
        log("Module URL did not visibly change after click. Continuing with current page.")

    if is_real_url(module_url):
        log(f"Captured module URL for refresh: {module_url}")
    else:
        module_url = page.url
        log(f"Using current page URL for refresh: {module_url}")

    if not is_real_url(module_url):
        raise RuntimeError(
            "Could not determine a real module URL for refresh. Add "
            "NTULEARN_MODULE_URL to .env using the course page URL."
        )

    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")
    return module_url


def expand_attendance_folder(page: Page, folder_name: str) -> None:
    log(f"Expanding folder: {folder_name}")

    folder_button = None
    for _ in range(12):
        folder_button = first_visible(
            page,
            [
                f'button[aria-label="Open Folder, {folder_name}"]',
                f'button:has-text("{folder_name}")',
                f'[data-analytics-id="content.item.folder.toggleFolder.button"]:has-text("{folder_name}")',
                *ATTENDANCE_FOLDER_SELECTORS,
            ],
            timeout_ms=1000,
        )
        if folder_button is not None:
            break
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(500)

    if folder_button is None:
        raise RuntimeError(f"Could not find folder button: {folder_name}")

    aria_expanded = folder_button.get_attribute("aria-expanded")
    if aria_expanded == "true":
        log(f"{folder_name} is already expanded.")
    else:
        folder_button.scroll_into_view_if_needed(timeout=5000)
        folder_button.click(timeout=10000)
        log(f"Clicked {folder_name}.")

    page.wait_for_timeout(1000)
    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")


def click_current_tutorial_attendance(page: Page) -> bool:
    log("Looking for the active numbered tutorial attendance item...")

    attendance_link = page.get_by_role("link", name=TUTORIAL_ATTENDANCE_PATTERN).first
    try:
        attendance_link.wait_for(state="visible", timeout=1500)
    except PlaywrightTimeoutError:
        log("Not found on current view. Scrolling down once...")
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(500)
        try:
            attendance_link.wait_for(state="visible", timeout=1500)
        except PlaywrightTimeoutError:
            log("No active numbered tutorial attendance item found.")
            return False

    log("Found active numbered tutorial attendance item.")

    attendance_name = attendance_link.inner_text(timeout=5000).strip()
    log(f"Clicking active attendance item: {attendance_name}")
    attendance_link.scroll_into_view_if_needed(timeout=5000)
    attendance_link.click(timeout=10000)
    log("Clicked active attendance item.")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")
    return True


def click_attendance_records(page: Page, records_name: str) -> bool:
    log(f"Looking for attendance records item: {records_name}")

    records_link = page.get_by_role("link", name=records_name).first
    try:
        records_link.wait_for(state="visible", timeout=1500)
    except PlaywrightTimeoutError:
        log("Attendance records not found on current view. Scrolling down once...")
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(500)
        try:
            records_link.wait_for(state="visible", timeout=1500)
        except PlaywrightTimeoutError:
            log("Attendance records item not found.")
            return False

    records_text = records_link.inner_text(timeout=5000).strip()
    log(f"Found attendance records item: {records_text}")
    records_link.scroll_into_view_if_needed(timeout=5000)
    records_link.click(timeout=10000)
    log("Clicked attendance records item.")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")
    return True


def wait_for_tutorial_attendance(
    page: Page,
    folder_name: str,
    refresh_url: str,
    max_attempts: int,
    window_end: datetime | None,
) -> None:
    attempt = 1
    log("Refresh mode: immediate refresh after each failed attendance search.")
    log(f"Refresh URL: {refresh_url}")
    while True:
        if window_end is not None and not attendance_window_is_open(window_end):
            raise RuntimeError("Attendance window closed before the item was found.")

        max_attempts_label = "unlimited" if max_attempts <= 0 else str(max_attempts)
        log(f"Attendance check attempt {attempt}/{max_attempts_label}")

        expand_attendance_folder(page, folder_name)
        if click_current_tutorial_attendance(page):
            return

        if max_attempts > 0 and attempt >= max_attempts:
            raise RuntimeError(
                "Active tutorial attendance item was not found before max attempts."
            )
        if window_end is not None and not attendance_window_is_open(window_end):
            raise RuntimeError("Attendance window closed before the item was found.")

        log("Attendance item not found. Refreshing immediately...")
        log(f"Reloading: {refresh_url}")
        page.goto(refresh_url, wait_until="domcontentloaded", timeout=60000)
        log("Reload complete.")
        page.wait_for_timeout(2000)
        attempt += 1


def perform_login(page: Page, username: str, password: str) -> None:
    log(f"Opening {NTU_LEARN_URL}")
    page.goto(NTU_LEARN_URL, wait_until="domcontentloaded", timeout=60000)

    maybe_submit_username(page, username)
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    maybe_submit_password(page, password)
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    maybe_confirm_stay_signed_in(page)

    log(
        "\nIf the browser asks for MFA, CAPTCHA, or a school-specific prompt, "
        "complete it in the opened browser window."
    )
    wait_after_login(page)

    courses_link = first_visible(page, COURSES_NAV_SELECTORS, timeout_ms=10000)
    if courses_link is None:
        log("Courses link was not visible after waiting. Continuing anyway...")
    else:
        log("Courses link is visible.")

    log(f"Current page title: {page.title()!r}")
    log(f"Current URL: {page.url}")


def run_flow(
    click_courses_button: bool = True,
    click_target_module: bool = True,
    expand_attendance: bool = True,
    click_attendance_item: bool = True,
    click_records_item: bool = False,
    keep_checking: bool = True,
    enforce_schedule: bool = False,
) -> None:
    load_dotenv(ENV_PATH)

    username = require_env("NTULEARN_USERNAME")
    password = require_env("NTULEARN_PASSWORD")
    headless = env_bool("HEADLESS", False)
    slow_mo_ms = int(os.getenv("SLOW_MO_MS", "100"))
    module_name = os.getenv("NTULEARN_MODULE_NAME", DEFAULT_MODULE_NAME)
    attendance_folder_name = os.getenv(
        "NTULEARN_ATTENDANCE_FOLDER_NAME",
        DEFAULT_ATTENDANCE_FOLDER_NAME,
    )
    attendance_records_name = os.getenv(
        "NTULEARN_ATTENDANCE_RECORDS_NAME",
        DEFAULT_ATTENDANCE_RECORDS_NAME,
    )
    max_check_attempts = env_int("MAX_CHECK_ATTEMPTS", 0)
    module_url_override = os.getenv("NTULEARN_MODULE_URL")
    window_end = wait_until_attendance_window() if enforce_schedule else None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        context = browser.new_context()
        page = context.new_page()

        perform_login(page, username, password)
        if click_courses_button:
            click_courses(page)
        module_url = page.url
        if click_target_module:
            module_url = click_module(page, module_name, module_url_override)
        if keep_checking and click_attendance_item:
            wait_for_tutorial_attendance(
                page,
                attendance_folder_name,
                module_url,
                max_check_attempts,
                window_end,
            )
        elif expand_attendance:
            expand_attendance_folder(page, attendance_folder_name)
            if click_records_item and not click_attendance_records(
                page,
                attendance_records_name,
            ):
                raise RuntimeError(f"Could not find attendance records link: {attendance_records_name}")
            if click_attendance_item and not click_current_tutorial_attendance(page):
                raise RuntimeError(
                    "Could not find active tutorial attendance link matching "
                    "`Tutorial <number> attendance`."
                )

        if not headless:
            input("Press Enter to close the browser...")

        browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NTU Learn attendance helper")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("login", help="Log in to NTU Learn only")
    subparsers.add_parser("courses", help="Log in and click Courses")
    subparsers.add_parser("module", help="Log in, click Courses, and click the target module")
    subparsers.add_parser(
        "attendance-folder",
        help="Log in, open the module, and expand the attendance folder",
    )
    subparsers.add_parser(
        "attendance-item",
        help="Open the active numbered tutorial attendance item once",
    )
    subparsers.add_parser(
        "attendance-records",
        help="Open the always-present tutorial attendance records item",
    )
    subparsers.add_parser(
        "watch",
        help="Keep refreshing until the active tutorial attendance item appears",
    )
    parser.set_defaults(command="watch")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.command == "login":
            run_flow(
                click_courses_button=False,
                click_target_module=False,
                expand_attendance=False,
                click_attendance_item=False,
                click_records_item=False,
                keep_checking=False,
            )
            return 0
        if args.command == "courses":
            run_flow(
                click_courses_button=True,
                click_target_module=False,
                expand_attendance=False,
                click_attendance_item=False,
                click_records_item=False,
                keep_checking=False,
            )
            return 0
        if args.command == "module":
            run_flow(
                click_courses_button=True,
                click_target_module=True,
                expand_attendance=False,
                click_attendance_item=False,
                click_records_item=False,
                keep_checking=False,
            )
            return 0
        if args.command == "attendance-folder":
            run_flow(
                click_courses_button=True,
                click_target_module=True,
                expand_attendance=True,
                click_attendance_item=False,
                click_records_item=False,
                keep_checking=False,
            )
            return 0
        if args.command == "attendance-item":
            run_flow(
                click_courses_button=True,
                click_target_module=True,
                expand_attendance=True,
                click_attendance_item=True,
                click_records_item=False,
                keep_checking=False,
            )
            return 0
        if args.command == "attendance-records":
            run_flow(
                click_courses_button=True,
                click_target_module=True,
                expand_attendance=True,
                click_attendance_item=False,
                click_records_item=True,
                keep_checking=False,
            )
            return 0
        if args.command == "watch":
            run_flow(
                click_courses_button=True,
                click_target_module=True,
                expand_attendance=True,
                click_attendance_item=True,
                click_records_item=False,
                keep_checking=True,
                enforce_schedule=True,
            )
            return 0
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
