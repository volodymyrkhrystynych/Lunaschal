"""Merged pull requests on the app's own repository, filed into the Journal.

Each PR merged into `settings.pr_journal_repo` becomes one ordinary journal
entry dated at its merge, listing how many of its commits landed on each 4am
day — so the feed shows when the work was done, not only when it shipped. A
model summary of the PR body is written into it afterwards, as P2 work.

`merged_prs` is the ledger: a PR with a row is never journaled again, so an
entry the user deletes stays deleted.
"""
