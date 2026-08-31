"""The one-off that turns a Markdown company list into board sources.

It has no route and no button, so a test is the only thing standing between a
bad regex and 140 silently-wrong saved searches.
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'import_company_boards',
    Path(__file__).resolve().parent.parent.parent / 'scripts' / 'import-company-boards.py',
)
importer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(importer)


DOC = """# Companies

## A

- **[Ada](https://www.jobfairr.com/companies/ada)** — Support automation. **5 jobs**. [Careers](https://job-boards.greenhouse.io/ada18)

- **[AlayaCare](https://x/alayacare)** — Home care software. **14 jobs**. [Careers](https://job-boards.greenhouse.io/embed/job_board?for=alayacare)

- **[1Password](https://x/1password)** — Passwords. **40 jobs**. [Careers](https://jobs.ashbyhq.com/1password)

- **[Achievers](https://x/achievers)** — Recognition. **14 jobs**. [Careers](https://jobs.lever.co/achievers)

- **[Abbott](https://x/abbott)** — Devices. **22 jobs**. [Careers](https://abbott.wd5.myworkdayjobs.com/abbottcareers?Location_Country=a30)

- **[1VALET](https://x/1valet)** — Buildings. **11 jobs**. [Careers](https://1valet.bamboohr.com/careers)

- **[Architech](https://x/architech)** — Consulting. **3 jobs**. [Careers](https://recruiting.ultipro.ca/ARC5003ASCS/JobBoard/23a42c6a)

Not a company line at all, just prose.
"""


def test_parses_every_entry_that_has_a_careers_link():
    entries = importer.parse_entries(DOC)
    assert len(entries) == 7
    assert entries[0] == ('Ada', 'https://job-boards.greenhouse.io/ada18')


def test_reads_the_slug_out_of_each_board_shape():
    """Including the Greenhouse embed form, where the slug is a query param."""
    by_company = {
        row['company']: row
        for row in (importer.classify(n, u) for n, u in importer.parse_entries(DOC))
    }
    assert by_company['Ada']['kind'] == 'greenhouse'
    assert by_company['Ada']['slug'] == 'ada18'
    assert by_company['AlayaCare']['slug'] == 'alayacare'
    assert by_company['1Password'] == {
        'company': '1Password', 'url': 'https://jobs.ashbyhq.com/1password',
        'kind': 'ashby', 'slug': '1password',
    }
    assert by_company['Achievers']['kind'] == 'lever'


def test_workday_is_syncable_even_though_the_resolver_declines_it():
    """`workday_watch.py` polls these; `job_searches.kind` cannot hold them."""
    row = importer.classify('Abbott', 'https://abbott.wd5.myworkdayjobs.com/abbottcareers?x=1')
    assert row['kind'] == 'workday'
    assert row['params'] == {'host': 'abbott.wd5.myworkdayjobs.com',
                             'tenant': 'abbott', 'site': 'abbottcareers'}


def test_an_unsyncable_ats_is_named_rather_than_called_unrecognised():
    """"We found BambooHR and cannot read it" is actionable; "nothing" is not."""
    assert importer.classify('1VALET', 'https://1valet.bamboohr.com/careers')['detected'] == 'BambooHR'
    assert importer.classify('X', 'https://recruiting.ultipro.ca/ARC/JobBoard')['detected'] == ''


def test_plan_deduplicates_two_companies_sharing_one_board():
    doc = DOC + """
- **[Ada Again](https://x/ada2)** — Same board. **5 jobs**. [Careers](https://job-boards.greenhouse.io/ada18)
"""
    syncable, skipped = importer.plan(importer.parse_entries(doc))
    slugs = [row['slug'] for row in syncable if row['kind'] == 'greenhouse']
    assert slugs.count('ada18') == 1
    assert any('duplicate' in (row.get('detected') or '') for row in skipped)


def test_plan_splits_syncable_from_skipped():
    syncable, skipped = importer.plan(importer.parse_entries(DOC))
    assert {row['kind'] for row in syncable} == {'greenhouse', 'ashby', 'lever', 'workday'}
    assert len(syncable) == 5
    assert len(skipped) == 2  # BambooHR, and the unrecognised UltiPro


@pytest.mark.usefixtures('client')
def test_commit_writes_both_tables_and_is_idempotent():
    from backend.db.connection import get_db

    syncable, _ = importer.plan(importer.parse_entries(DOC))
    first = importer.commit(syncable, interval_hours=24, enabled=True)
    assert first['added'] == 5

    db = get_db()
    assert db.execute('SELECT COUNT(*) c FROM job_searches').fetchone()['c'] == 4
    assert db.execute('SELECT COUNT(*) c FROM workday_boards').fetchone()['c'] == 1

    # Re-running must not double every source: the list will be re-reviewed.
    second = importer.commit(syncable, interval_hours=24, enabled=True)
    assert second['added'] == 0
    assert db.execute('SELECT COUNT(*) c FROM job_searches').fetchone()['c'] == 4


@pytest.mark.usefixtures('client')
def test_commit_can_create_sources_switched_off():
    from backend.db.connection import get_db

    syncable, _ = importer.plan(importer.parse_entries(DOC))
    importer.commit(syncable, interval_hours=24, enabled=False)
    db = get_db()
    assert db.execute('SELECT SUM(enabled) s FROM job_searches').fetchone()['s'] == 0
