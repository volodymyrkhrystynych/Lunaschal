"""Use PressReader's subscriber UI, including its full-issue PDF choice.

No private PDF endpoint, paywall workaround, password storage, or page-printing
fallback. The browser state is created by the user's own interactive sign-in.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.newspapers import issues

HOST = 'https://torontostar.pressreader.com'
ISSUE_LABEL = re.compile(r'^Download issue (?:as|in) PDF$', re.I)


class DownloadError(Exception):
    pass


class SignInRequired(DownloadError):
    pass


class BrowserMissing(DownloadError):
    pass


# Worker exit codes; anything else nonzero is a generic failure.
EXIT_SIGN_IN = 2
EXIT_BROWSER_MISSING = 3

# '0' keeps the browsers inside the playwright package in the venv rather than
# ~/.cache/ms-playwright, which gets purged -- twice, silently breaking every
# download. It also ties the browser's lifetime to the package it must match.
INSTALL_COMMAND = 'PLAYWRIGHT_BROWSERS_PATH=0 .venv/bin/python -m playwright install chromium'


def _playwright_env():
    return {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or '0'}


def _use_durable_browsers():
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _playwright_env()['PLAYWRIGHT_BROWSERS_PATH']


def session_path():
    return Path(os.environ.get('PRESSREADER_SESSION_PATH', './data/pressreader/session.json')).expanduser().resolve()


def issue_url(date):
    return f"{HOST}/toronto-star/{issues.validate_date(date).replace('-', '')}/page/1"


def save_session(context):
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            name = output.name
            os.chmod(name, 0o600)
            json.dump(context.storage_state(indexed_db=True), output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def open_pdf_menu(page, date):
    page.goto(issue_url(date), wait_until='domcontentloaded', timeout=45000)
    page.get_by_role('button', name='Options', exact=True).click()
    menu = page.get_by_role('menuitem', name='Download as PDF', exact=True)
    menu.wait_for(state='visible')
    if page.get_by_role('button', name='Sign in', exact=True).is_visible() or menu.is_disabled():
        raise SignInRequired('Sign in to PressReader again and confirm your subscription allows PDF downloads.')
    # Do not archive a redirect to a different edition under the requested date.
    if not page.url.startswith(issue_url(date).rsplit('/page/', 1)[0] + '/'):
        raise DownloadError('PressReader opened a different edition. This issue may not be available yet.')
    menu.click()


def download_from_page(page, date):
    """Returns a Playwright Download; caller consumes it before closing context."""
    open_pdf_menu(page, date)
    # An anchored label prevents selecting the adjacent 'Download page as PDF'.
    choice = page.get_by_text(ISSUE_LABEL).first
    choice.wait_for(state='visible')
    with page.expect_download(timeout=120000) as pending:
        choice.click()
        # The current dialog has an issue-labelled title and a plain Download
        # action (a span inside a link). Scope that action to the issue dialog;
        # never choose a generic Download elsewhere or the page-PDF option.
        dialog = page.get_by_role('dialog').filter(has=page.get_by_text(ISSUE_LABEL))
        confirm = dialog.get_by_text('Download', exact=True).or_(
            dialog.get_by_role('button', name=ISSUE_LABEL)
        )
        try:
            confirm.wait_for(state='visible', timeout=5000)
        except Exception as exc:
            from playwright.sync_api import TimeoutError as BrowserTimeout
            if not isinstance(exc, BrowserTimeout):
                raise
        else:
            confirm.click()
    return pending.value


def fetch_to_file(date, destination):
    from playwright.sync_api import sync_playwright, Error as BrowserError
    from pypdf import PdfReader
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(downloads_path=str(destination.parent))
            except BrowserError as exc:
                if "Executable doesn't exist" in str(exc):
                    raise BrowserMissing('The PressReader browser is not installed.') from None
                raise
            try:
                session_version = session_path().stat().st_mtime_ns
                context = browser.new_context(storage_state=str(session_path()), accept_downloads=True, locale='en-US')
                page = context.new_page()
                page.set_default_timeout(20000)
                download = download_from_page(page, date)
                path = download.path()  # Wait for the completed download, before archive publication.
                if path is None:
                    raise DownloadError('PressReader did not return a PDF file. Please retry.')
                count = re.search(r'\bof\s+(\d+)\b', page.get_by_role('banner').get_by_role('heading', level=1).inner_text())
                if count is None or len(PdfReader(path).pages) != int(count[1]):
                    raise DownloadError('Downloaded PDF does not match the full issue page count.')
                download.save_as(str(destination))
                # An interactive reconnect must not be overwritten by an older worker.
                if session_path().stat().st_mtime_ns == session_version:
                    save_session(context)
            finally:
                browser.close()
    except BrowserError:
        # Playwright errors can contain signed download URLs. Never expose them
        # through logs, SQLite, or the UI.
        raise DownloadError('PressReader download did not finish. Check the browser installation, reconnect your subscription, or retry later.') from None


def _run(argv, timeout, env):
    """Run a child in its own process group; None on timeout (group killed)."""
    process = subprocess.Popen(
        argv, cwd=Path(__file__).resolve().parents[2], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return None


def _run_fetch(date, destination):
    env = {**_playwright_env(), 'PRESSREADER_SESSION_PATH': str(session_path())}
    code = _run([sys.executable, '-m', 'backend.newspapers.pressreader', 'fetch', date, str(destination)], 240, env)
    if code is None:
        raise DownloadError('Issue download timed out after four minutes. Please retry later.')
    return code


def install_browser():
    """Headless shell only: that is all the worker launches. login() needs full
    chromium, which a graphical setup installs by hand (INSTALL_COMMAND)."""
    code = _run([sys.executable, '-m', 'playwright', 'install', 'chromium-headless-shell'], 600, _playwright_env())
    if code != 0:
        raise DownloadError('Could not install the browser PressReader downloads need. Check the network and retry later.')


def download_issue(date):
    """Bound the entire browser/download lifetime, including transfer completion."""
    if not session_path().is_file():
        raise SignInRequired('Connect your Toronto Star subscription before downloading.')
    import importlib.util
    if importlib.util.find_spec('playwright') is None:
        raise DownloadError('Install requirements-pressreader.txt and the Playwright Chromium browser.')
    issues.validate_date(date)
    root = issues.archive_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pressreader-', dir=root) as temp:
        destination = Path(temp) / 'issue.pdf'
        code = _run_fetch(date, destination)
        if code == EXIT_BROWSER_MISSING:
            # A purged cache or a playwright upgrade; reinstall once, retry once.
            install_browser()
            code = _run_fetch(date, destination)
            if code == EXIT_BROWSER_MISSING:
                raise DownloadError('The PressReader browser is still missing after reinstalling it. Please retry later.')
        if code == EXIT_SIGN_IN:
            raise SignInRequired('Sign in to PressReader again and confirm your subscription allows PDF downloads.')
        if code != 0 or not destination.is_file():
            raise DownloadError('PressReader could not download the complete issue. Check the browser installation, reconnect, or retry later.')
        with destination.open('rb') as stream:
            return issues.store_issue(date, stream)


def login():
    _use_durable_browsers()
    from playwright.sync_api import sync_playwright, Error as BrowserError
    date = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except BrowserError as exc:
            if "Executable doesn't exist" not in str(exc):
                raise
            sys.exit(f'Chromium is not installed. From the repository root run:\n  {INSTALL_COMMAND}')
        try:
            context = browser.new_context(locale='en-US', storage_state=str(session_path()) if session_path().is_file() else None)
            page = context.new_page()
            page.goto(issue_url(date), wait_until='domcontentloaded')
            print('Sign in to your Toronto Star subscription in the browser. Return here when the issue is readable.')
            input('Press Enter to verify and save the session: ')
            open_pdf_menu(page, date)
            save_session(context)
            print('Subscriber session saved. You can now download issues from Newspapers.')
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Connect the Toronto Star subscriber browser session')
    parser.add_argument('command', choices=['login', 'fetch'])
    parser.add_argument('date', nargs='?')
    parser.add_argument('destination', nargs='?')
    args = parser.parse_args()
    _use_durable_browsers()
    if args.command == 'login':
        login()
    else:
        if not args.date or not args.destination:
            parser.error('fetch requires an issue date and destination')
        try:
            fetch_to_file(args.date, Path(args.destination))
        except SignInRequired:
            sys.exit(EXIT_SIGN_IN)
        except BrowserMissing:
            sys.exit(EXIT_BROWSER_MISSING)
        except Exception:
            sys.exit(1)
