from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.db.connection import get_db, _ensure_newspaper_downloads
from backend.newspapers import issues, pressreader, scheduler


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv('NEWSPAPERS_ARCHIVE_ROOT', str(tmp_path / 'archive'))
    (tmp_path / 'private').mkdir()
    monkeypatch.setenv('PRESSREADER_SESSION_PATH', str(tmp_path / 'private' / 'session.json'))


def pdf_bytes():
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def stamp(value):
    return int(datetime.fromisoformat(value).timestamp())


def job(date='2026-09-06'):
    return dict(get_db().execute('SELECT * FROM newspaper_downloads WHERE date=?', (date,)).fetchone())


def test_manual_queue_idempotent_and_worker_archives(client, monkeypatch):
    calls = []
    def download(date):
        calls.append(date)
        return issues.store_issue(date, BytesIO(pdf_bytes()))
    monkeypatch.setattr(pressreader, 'download_issue', download)
    for _ in range(2):
        assert client.post('/api/newspapers/issues/2026-09-06/download').status_code == 202
    assert job()['attempts'] == 0
    scheduler.tick()
    assert calls == ['2026-09-06']
    assert job()['status'] == 'complete'
    scheduler.tick()
    assert calls == ['2026-09-06']
    assert issues.issue_path('2026-09-06').is_file()


def toronto(date, minute):
    """Unix time for `minute` past midnight on `date` in Toronto."""
    from zoneinfo import ZoneInfo
    local = datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('America/Toronto'))
    return int(local.timestamp()) + minute * 60


def enable_auto_download():
    get_db().execute('UPDATE settings SET newspapers_auto_download=1 WHERE id=1')
    get_db().commit()


def test_evening_minute_is_stable_and_inside_six_to_ten_pm():
    minutes = [scheduler.evening_minute(f'2026-09-{day:02d}') for day in range(1, 31)]
    assert all(18 * 60 <= m < 22 * 60 for m in minutes)
    assert len(set(minutes)) > 1  # actually random from day to day
    assert scheduler.evening_minute('2026-09-06') == scheduler.evening_minute('2026-09-06')


def test_daily_schedule_waits_for_the_random_evening_time_and_deduplicates(monkeypatch):
    enable_auto_download()
    monkeypatch.setattr(pressreader, 'download_issue', lambda date: issues.store_issue(date, BytesIO(pdf_bytes())))
    at = scheduler.evening_minute('2026-09-06')
    scheduler.tick(toronto('2026-09-06', 6 * 60))
    scheduler.tick(toronto('2026-09-06', at - 1))
    assert get_db().execute('SELECT count(*) FROM newspaper_downloads').fetchone()[0] == 0
    scheduler.tick(toronto('2026-09-06', at))
    scheduler.tick(toronto('2026-09-06', at + 60))
    assert job()['attempts'] == 1
    assert job()['status'] == 'complete'


def test_evening_schedule_respects_the_auto_download_setting():
    scheduler.tick(toronto('2026-09-06', 22 * 60 - 1))
    assert get_db().execute('SELECT count(*) FROM newspaper_downloads').fetchone()[0] == 0


def test_first_journal_entry_queues_todays_issue(client, monkeypatch):
    enable_auto_download()
    monkeypatch.setattr(scheduler.time, 'time', lambda: toronto('2026-09-06', 9 * 60))
    assert client.post('/api/journal', json={'content': 'morning'}).status_code == 201
    assert job()['status'] == 'queued'
    assert client.post('/api/journal', json={'content': 'again'}).status_code == 201
    assert get_db().execute('SELECT count(*) FROM newspaper_downloads').fetchone()[0] == 1


def test_journal_entry_does_not_queue_without_auto_download(client):
    assert client.post('/api/journal', json={'content': 'morning'}).status_code == 201
    assert get_db().execute('SELECT count(*) FROM newspaper_downloads').fetchone()[0] == 0


def test_journal_trigger_after_two_failures_does_not_retry_again(client, monkeypatch):
    enable_auto_download()
    def fail(date):
        raise pressreader.DownloadError('Not yet available')
    monkeypatch.setattr(pressreader, 'download_issue', fail)
    now = toronto('2026-09-06', 9 * 60)
    monkeypatch.setattr(scheduler.time, 'time', lambda: now)
    client.post('/api/journal', json={'content': 'morning'})
    scheduler.tick(now)
    scheduler.tick(now + 3600)
    assert job()['attempts'] == 2
    client.post('/api/journal', json={'content': 'later'})
    scheduler.tick(toronto('2026-09-06', 22 * 60 - 1))
    assert job()['attempts'] == 2
    assert job()['status'] == 'failed'


def test_failed_download_backoff_and_explicit_retry(client, monkeypatch):
    def fail(date):
        raise pressreader.DownloadError('Issue not available yet')
    monkeypatch.setattr(pressreader, 'download_issue', fail)
    scheduler.queue_issue('2026-09-06')
    now = stamp('2026-09-06T10:00:00+00:00')
    scheduler.tick(now)
    scheduler.tick(now + 60)
    assert job()['attempts'] == 1
    for hour in range(1, 6):
        scheduler.tick(now + hour * 3600)
    assert job()['attempts'] == 2
    assert job()['status'] == 'failed'
    client.post('/api/newspapers/issues/2026-09-06/download')
    assert job()['status'] == 'queued'
    assert job()['attempts'] == 0


def test_signin_failure_waits_for_reconnect(client, monkeypatch):
    scheduler.queue_issue('2026-09-06')
    scheduler.tick()
    assert job()['status'] == 'sign-in-required'
    scheduler.tick()
    assert job()['attempts'] == 1
    assert client.get('/api/newspapers/pressreader').json['sessionSaved'] is False
    pressreader.session_path().write_text('{}')
    import os
    os.utime(pressreader.session_path(), (job()['updated_at'] + 1,) * 2)
    monkeypatch.setattr(pressreader, 'download_issue', lambda date: issues.store_issue(date, BytesIO(pdf_bytes())))
    scheduler.tick()
    assert job()['status'] == 'complete'


def test_restart_recovers_without_redownloading_published_pdf(monkeypatch):
    scheduler.queue_issue('2026-09-06')
    get_db().execute("UPDATE newspaper_downloads SET status='downloading'")
    get_db().commit()
    issues.store_issue('2026-09-06', BytesIO(pdf_bytes()))
    _ensure_newspaper_downloads(get_db())
    assert job()['status'] == 'queued'
    def forbidden(date):
        pytest.fail('Published issue should not be downloaded again')
    monkeypatch.setattr(pressreader, 'download_issue', forbidden)
    scheduler.tick()
    assert job()['status'] == 'complete'


def test_status_does_not_expose_session_and_configuration_is_validated(client):
    pressreader.session_path().write_text('{"cookies": [{"value":"PRIVATE-COOKIE"}]}')
    assert 'PRIVATE-COOKIE' not in client.get('/api/newspapers/pressreader').text
    assert client.put('/api/newspapers/pressreader', json={'autoDownload': 'true'}).status_code == 400
    assert client.put('/api/newspapers/pressreader', json={'autoDownload': True}).json['autoDownload'] is True
    assert client.post('/api/newspapers/issues/2099-01-01/download').status_code == 400
    assert client.post('/api/newspapers/issues/invalid/download').status_code == 400


def test_unexpected_provider_errors_do_not_leak_secrets(monkeypatch):
    def fail(date):
        raise RuntimeError('https://example.org?token=SECRET')
    monkeypatch.setattr(pressreader, 'download_issue', fail)
    scheduler.queue_issue('2026-09-06')
    scheduler.tick()
    assert 'SECRET' not in job()['error']


def test_session_file_is_private_and_atomically_replaced():
    class Context:
        def storage_state(self, **kwargs):
            return {'cookies': [], 'origins': []}
    pressreader.save_session(Context())
    assert pressreader.session_path().stat().st_mode & 0o777 == 0o600
    assert len(list(pressreader.session_path().parent.iterdir())) == 1


def test_automatic_queue_does_not_busy_spin_on_a_failed_issue(monkeypatch):
    enable_auto_download()
    def fail(date):
        raise pressreader.DownloadError('Not yet available')
    monkeypatch.setattr(pressreader, 'download_issue', fail)
    scheduler._wake.clear()
    scheduler.tick(toronto('2026-09-06', 22 * 60 - 2))
    assert not scheduler._wake.is_set()
    scheduler.tick(toronto('2026-09-06', 22 * 60 - 1))
    assert not scheduler._wake.is_set()
    assert job()['attempts'] == 1


def test_browser_process_deadline_kills_group_and_cleans_temporary_files(monkeypatch):
    pytest.importorskip('playwright')
    import subprocess
    import signal
    pressreader.session_path().write_text('{}')
    calls = []
    class Process:
        pid = 12345
        def wait(self, timeout=None):
            if timeout:
                assert timeout == 240
                raise subprocess.TimeoutExpired('browser', timeout)
            return -9
    monkeypatch.setattr(pressreader.subprocess, 'Popen', lambda *a, **kw: Process())
    monkeypatch.setattr(pressreader.os, 'killpg', lambda *args: calls.append(args))
    with pytest.raises(pressreader.DownloadError, match='timed out'):
        pressreader.download_issue('2026-09-06')
    assert calls == [(12345, signal.SIGKILL)]
    assert not list(issues.archive_root().iterdir())


def test_child_signin_error_is_not_retried_as_a_network_failure(monkeypatch):
    pytest.importorskip('playwright')
    pressreader.session_path().write_text('{}')
    class Process:
        def wait(self, **kwargs):
            return 2
    monkeypatch.setattr(pressreader.subprocess, 'Popen', lambda *a, **kw: Process())
    with pytest.raises(pressreader.SignInRequired):
        pressreader.download_issue('2026-09-06')


def _fake_children(monkeypatch, fetch_codes, install_code=0):
    """Popen stand-in: the fetch worker exits with fetch_codes in turn (writing a
    PDF on 0); the browser installer exits with install_code."""
    calls = []
    codes = iter(fetch_codes)
    class Process:
        pid = 4242
        def __init__(self, argv, env, **kwargs):
            self.argv = argv
            calls.append((argv, env))
        def wait(self, timeout=None):
            if 'playwright' in self.argv:
                return install_code
            code = next(codes)
            if code == 0:
                Path(self.argv[-1]).write_bytes(pdf_bytes())
            return code
    monkeypatch.setattr(pressreader.subprocess, 'Popen', Process)
    return calls


def test_missing_browser_is_reinstalled_into_the_venv_and_the_fetch_retried(monkeypatch):
    pytest.importorskip('playwright')
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    pressreader.session_path().write_text('{}')
    calls = _fake_children(monkeypatch, [pressreader.EXIT_BROWSER_MISSING, 0])
    pressreader.download_issue('2026-09-06')
    argvs = [argv for argv, env in calls]
    assert argvs[1][1:] == ['-m', 'playwright', 'install', 'chromium-headless-shell']
    assert [a[3] for a in (argvs[0], argvs[2])] == ['fetch', 'fetch']
    # Worker and installer must agree on where the browser lives, or the
    # install lands somewhere the worker never looks.
    assert {env['PLAYWRIGHT_BROWSERS_PATH'] for argv, env in calls} == {'0'}
    assert issues.get_issue('2026-09-06')


def test_explicit_browsers_path_is_respected(monkeypatch):
    pytest.importorskip('playwright')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', '/opt/browsers')
    pressreader.session_path().write_text('{}')
    calls = _fake_children(monkeypatch, [0])
    pressreader.download_issue('2026-09-06')
    assert calls[0][1]['PLAYWRIGHT_BROWSERS_PATH'] == '/opt/browsers'


def test_failed_browser_install_is_reported_as_such(monkeypatch):
    pytest.importorskip('playwright')
    pressreader.session_path().write_text('{}')
    calls = _fake_children(monkeypatch, [pressreader.EXIT_BROWSER_MISSING], install_code=1)
    with pytest.raises(pressreader.DownloadError, match='Could not install the browser'):
        pressreader.download_issue('2026-09-06')
    assert len(calls) == 2


def test_browser_still_missing_after_install_is_not_retried_again(monkeypatch):
    pytest.importorskip('playwright')
    pressreader.session_path().write_text('{}')
    missing = pressreader.EXIT_BROWSER_MISSING
    calls = _fake_children(monkeypatch, [missing, missing])
    with pytest.raises(pressreader.DownloadError, match='still missing'):
        pressreader.download_issue('2026-09-06')
    assert sum(argv[3:4] == ['fetch'] for argv, env in calls) == 2


def test_browser_install_deadline_kills_its_group(monkeypatch):
    pytest.importorskip('playwright')
    import subprocess
    import signal
    pressreader.session_path().write_text('{}')
    killed = []
    class Process:
        pid = 777
        def __init__(self, argv, **kwargs):
            self.argv = argv
        def wait(self, timeout=None):
            if 'playwright' in self.argv and timeout:
                assert timeout == 600
                raise subprocess.TimeoutExpired('install', timeout)
            return pressreader.EXIT_BROWSER_MISSING if timeout else -9
    monkeypatch.setattr(pressreader.subprocess, 'Popen', Process)
    monkeypatch.setattr(pressreader.os, 'killpg', lambda *args: killed.append(args))
    with pytest.raises(pressreader.DownloadError, match='Could not install'):
        pressreader.download_issue('2026-09-06')
    assert killed == [(777, signal.SIGKILL)]


def test_worker_maps_a_missing_executable_to_its_own_exit_code(monkeypatch, tmp_path):
    sync_api = pytest.importorskip('playwright.sync_api')
    class Chromium:
        def launch(self, **kwargs):
            raise sync_api.Error("BrowserType.launch: Executable doesn't exist at /nowhere/chrome")
    class Playwright:
        chromium = Chromium()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(sync_api, 'sync_playwright', lambda: Playwright())
    with pytest.raises(pressreader.BrowserMissing):
        pressreader.fetch_to_file('2026-09-06', tmp_path / 'issue.pdf')


@pytest.mark.parametrize('confirmation', [
    '<button onclick="location.href=\'/issue.pdf\'">Download issue as PDF</button>',
    '<h2>Download Issue as PDF</h2><a href="/issue.pdf"><span>Download</span></a>',
])
def test_browser_selects_issue_pdf_not_page_pdf(confirmation):
    """Real DOM/click/download test; all PressReader requests are intercepted."""
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip('Install Playwright Chromium for the browser contract test')
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(accept_downloads=True)
            hits = []
            html = '''<header role="banner"><h1>6 Sep 2026·1 of 3</h1>
              <button onclick="document.querySelector('nav').hidden=false">Options</button></header>
              <nav hidden><button role="menuitem" onclick="document.querySelector('section').hidden=false">Download as PDF</button></nav>
              <section hidden><a href="/page.pdf">Download page as PDF</a>
              <a href="#" onclick="document.querySelector('dialog').showModal()">Download issue as PDF</a></section>
              <dialog>CONFIRMATION</dialog>'''.replace('CONFIRMATION', confirmation)
            def route_handler(route):
                if route.request.url.endswith('.pdf'):
                    hits.append(route.request.url)
                    route.fulfill(body=pdf_bytes(), headers={'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename=issue.pdf'})
                else:
                    route.fulfill(body=html, content_type='text/html')
            context.route('**/*', route_handler)
            download = pressreader.download_from_page(context.new_page(), '2026-09-06')
            assert Path(download.path()).read_bytes().startswith(b'%PDF-')
            assert hits == [pressreader.HOST + '/issue.pdf']
        finally:
            browser.close()
