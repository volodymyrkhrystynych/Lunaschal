"""The failure report that goes ahead of a test run's tail."""
from backend.codeagent import testreport


def _batch(n, total, body):
    return [f'== backend test batch {n}/{total} (15 files) ==',
            '============================= test session starts ==============================',
            *body]


GREEN = ['backend/tests/test_a.py ....                     [100%]',
         '============================= 4 passed in 1.20s =============================']


def batched_run():
    lines = []
    lines += _batch(1, 4, GREEN)
    lines += _batch(2, 4, [
        'backend/tests/test_b.py ..F.                     [100%]',
        '=================================== FAILURES ===================================',
        '___________________________________ test_two ___________________________________',
        'E   AssertionError: assert 1 == 2',
        '=========================== short test summary info ============================',
        'FAILED backend/tests/test_b.py::test_two - AssertionError: assert 1 == 2',
        '========================= 1 failed, 3 passed in 0.90s ==========================',
    ])
    lines += _batch(3, 4, [
        'backend/tests/test_main.py ',
        'Fatal Python error: Aborted',
        'Current thread 0x00007f (most recent call first):',
        '  File "/work/main.py", line 40 in _start_window',
    ])
    # Lots of green after, so the failures are far from the end.
    lines += _batch(4, 4, GREEN * 300)
    lines.append('2/4 batch(es) had failures')
    return '\n'.join(lines)


def test_failing_batches_are_named_with_their_failures():
    found = testreport.failures(batched_run())
    assert found[0] == '== backend test batch 2/4 (15 files) =='
    assert 'FAILED backend/tests/test_b.py::test_two - AssertionError: assert 1 == 2' in found
    assert '========================= 1 failed, 3 passed in 0.90s ==========================' in found
    assert '== backend test batch 3/4 (15 files) ==' in found
    assert 'Fatal Python error: Aborted' in found
    assert '2/4 batch(es) had failures' in found
    # Green batches are not mentioned at all.
    assert not any('batch 1/4' in l or 'batch 4/4' in l for l in found)


def test_a_batch_with_no_pytest_summary_is_reported_as_crashed_with_context():
    found = testreport.failures(batched_run())
    i = found.index('== backend test batch 3/4 (15 files) ==')
    assert any('ended without a pytest summary' in l for l in found[i:])
    assert any('_start_window' in l for l in found[i:])


def test_the_report_puts_failures_before_a_tail_that_lost_them():
    text = batched_run()
    out = testreport.report(text, tail_lines=20)
    head, _, tail = out.partition('── end of the output ──')
    assert head.startswith('What failed:')
    assert 'test_b.py::test_two' in head
    assert 'test_b.py::test_two' not in tail  # the tail alone would not have had it
    assert tail.rstrip().endswith('2/4 batch(es) had failures')


def test_a_clean_run_is_just_its_tail():
    text = '\n'.join(GREEN * 3 + ['All 4 batches passed'])
    assert testreport.failures(text) == []
    assert testreport.report(text) == text


def test_vitest_failures_are_found():
    text = '\n'.join([
        ' ✓ src/lib/a.test.ts (3 tests) 4ms',
        ' FAIL  src/components/Tasks/Todo.test.tsx > records a to-do',
        '   × records a to-do 12ms',
        ' Test Files  1 failed | 228 passed (229)',
        '      Tests  1 failed | 2573 passed (2574)',
    ])
    found = testreport.failures(text)
    assert ' FAIL  src/components/Tasks/Todo.test.tsx > records a to-do' in found
    assert any('× records a to-do' in l for l in found)
    assert any('Test Files' in l for l in found)


def test_an_unbatched_pytest_run_reports_failed_lines_without_headers():
    text = '\n'.join(['test_x.py F', 'FAILED test_x.py::t - boom',
                      '=========== 1 failed in 0.1s ===========', 'trailing'])
    assert testreport.failures(text) == ['FAILED test_x.py::t - boom',
                                         '=========== 1 failed in 0.1s ===========']


def test_clip_keeps_the_failures_and_cuts_the_tail():
    out = testreport.report(batched_run(), tail_lines=200)
    clipped = testreport.clip_report(out, 1200)
    assert len(clipped) <= 1200
    assert 'test_b.py::test_two' in clipped
    assert clipped.rstrip().endswith('2/4 batch(es) had failures')


def test_clip_without_failures_keeps_the_end():
    assert testreport.clip_report('a' * 50 + 'END', 10) == 'a' * 7 + 'END'


def test_the_failure_list_is_bounded_and_says_it_was_cut():
    text = '\n'.join([f'FAILED t.py::case_{i} - x' for i in range(1000)]
                     + ['1/1 batch(es) had failures'])
    found = testreport.failures(text)
    assert len(found) == testreport.MAX_FAILURE_LINES
    assert found[-2].startswith('… ') and 'not shown' in found[-2]
    assert found[-1] == '1/1 batch(es) had failures'


def test_identical_summaries_in_two_batches_are_both_kept():
    bad = '==== 1 failed, 80 passed in 4.2s ===='
    text = '\n'.join(_batch(3, 7, [bad]) + _batch(7, 7, [bad]))
    found = testreport.failures(text)
    assert found.count(bad) == 2
    assert found.index('== backend test batch 7/7 (15 files) ==') < len(found) - 1


def test_output_after_the_runner_closes_is_not_the_last_batch():
    text = '\n'.join(_batch(1, 1, GREEN) + [
        'All 1 batches passed',
        ' FAIL  src/x.test.ts > case',
        ' Test Files  1 failed | 9 passed (10)',
    ])
    found = testreport.failures(text)
    assert not any('batch 1/1' in l for l in found)
    assert ' FAIL  src/x.test.ts > case' in found
