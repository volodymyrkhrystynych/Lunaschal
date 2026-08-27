"""The nightly pass that reads a repository and writes down what it found.

`plan_modules` is the whole policy in one function reading only the DB and the
snapshot's module index — the same shape as research_job.plan_next, and testable
without threads for the same reason.

The policy, and why:

- **Modules that changed since the last pass come first.** A note that no longer
  describes the code is worse than a missing one, because it will be retrieved
  and believed.
- **Then modules with no note at all, largest first.** The biggest undocumented
  module is the one most worth a note, and it is also the one a person is least
  likely to write up by hand.
- **A handful per night, not the whole repo.** A full churn on a 25 tok/s local
  model is hours of GPU time competing with everything else, and it rewrites
  articles that did not change. The wiki fills in over a week or two and then
  only tracks change.

One rule for everything here, inherited from research_job: never hold a
transaction across a model or tool call. get_db() hands out one process-global
connection, so a commit() in any Flask request handler would commit whatever
this thread had pending. Write, commit, then call the model.
"""
import logging
import time

from backend.db.connection import get_db, row_to_dict

logger = logging.getLogger(__name__)

DEFAULT_ARTICLES_PER_NIGHT = 6

# A module with almost nothing in it is not worth a model call; its files can
# simply be read when they come up.
MIN_MODULE_LINES = 40

# Reading a module is a long loop of small turns: a list_dir, a search, and
# several reads before anything is understood.
MODULE_MAX_TURNS = 24
MODULE_MAX_READS = 30


def module_slug(module_path: str) -> str:
    """A stable slug for a module's article.

    Derived from the path rather than from the model's chosen title, so a
    refresh lands on the same article every time. A retitled note is fine; a
    second article about the same directory is not.
    """
    from backend.research.wiki import slugify
    return slugify(module_path.replace('/', '-') or 'repository-root')


def _existing_articles(repo_id: str) -> dict[str, dict]:
    rows = get_db().execute(
        "SELECT * FROM wiki_articles WHERE repo_id=? AND kind='code'",
        (repo_id,),
    ).fetchall()
    return {r['slug']: row_to_dict(r) for r in rows}


def _changed_modules(root, since_sha: str | None) -> set[str]:
    """The module paths touched between `since_sha` and HEAD.

    An unusable range — a rebase, a branch switch — yields nothing rather than
    an error: "we cannot tell what changed" reads correctly as "refresh nothing
    on that basis", and the fill-in pass still runs.
    """
    if not since_sha:
        return set()
    from backend.repos.git import changed_files
    out = set()
    for path in changed_files(root, since_sha):
        parent = path.rsplit('/', 1)[0] if '/' in path else ''
        out.add(parent)
    return out


def plan_modules(
    repo_id: str,
    limit: int = DEFAULT_ARTICLES_PER_NIGHT,
    since_sha: str | None = None,
) -> list[dict]:
    """The modules to write up this run, in the order to do them.

    Returns [{path, reason, info, existing}] — `reason` is 'changed' or 'new',
    which is what the caller records in the revision log.
    """
    from backend.repos import registry, storage
    from backend.research.repo_job import current_snapshot

    snapshot = current_snapshot(repo_id)
    if not snapshot:
        return []
    modules = _snapshot_modules(snapshot)
    if not modules:
        return []

    repo = registry.get_repo(repo_id)
    root = storage.repo_dir(repo['slug']) if repo else None
    changed = _changed_modules(root, since_sha) if root else set()
    existing = _existing_articles(repo_id)

    refresh: list[dict] = []
    fresh: list[dict] = []
    for module in modules:
        if module.get('lines', 0) < MIN_MODULE_LINES:
            continue
        slug = module_slug(module['path'])
        article = existing.get(slug)
        if article is None:
            fresh.append({'path': module['path'], 'reason': 'new', 'info': module,
                          'existing': None})
        elif module['path'] in changed:
            refresh.append({'path': module['path'], 'reason': 'changed',
                            'info': module, 'existing': article})

    # Oldest note first among the stale ones: the note that has been wrong
    # longest is the one most likely to be believed by mistake.
    refresh.sort(key=lambda m: m['existing'].get('updatedAt') or '')
    # `modules` already arrives largest-first from the snapshot.
    return (refresh + fresh)[:limit]


def _snapshot_modules(snapshot: dict) -> list[dict]:
    import json
    try:
        facts = json.loads(snapshot.get('facts') or '{}')
    except (ValueError, TypeError):
        return []
    return facts.get('modules') or []


def write_module_article(
    repo: dict,
    target: dict,
    cancel=None,
    checkpoint=None,
) -> dict | None:
    """Read one module and write its article. Returns the article or None.

    None is a real outcome — the model saw too little to say anything useful —
    and leaves the module to be picked up again next run.
    """
    from backend.ai import code_wiki as prompts
    from backend.repos import storage
    from backend.research import agent, code, wiki

    root = storage.repo_dir(repo['slug'])
    if root is None or not root.is_dir():
        return None

    checkpoint = checkpoint or agent.make_checkpoint(cancel=cancel)
    tools = code.CodeTools(root, max_reads=MODULE_MAX_READS)

    result = agent.gather(
        prompts.GATHER_SYSTEM,
        prompts.build_gather_request(
            repo['name'], target['path'], target.get('info'), target.get('existing')
        ),
        tools=code.tools_for(root),
        dispatch=code.dispatch_for(tools, root),
        checkpoint=checkpoint,
        max_turns=MODULE_MAX_TURNS,
    )

    checkpoint()
    article = prompts.write_article(
        repo['name'], target['path'],
        prompts.flatten_transcript(result.get('messages') or []),
        tools.files_read,
    )
    if not article:
        logger.info('No code-wiki article for %s/%s', repo['slug'], target['path'])
        return None

    try:
        return wiki.upsert_article(
            module_slug(target['path']),
            article['title'],
            article['summary'],
            article['content'],
            repo_id=repo['id'],
            kind='code',
            # Provenance from what was opened, not from what the model says it
            # read — the rule agent._loop already applies to web_fetch.
            sources=code.files_read(result.get('steps') or []) or None,
            note=article.get('note') or f"{target['reason']} module pass",
        )
    except wiki.ArticleLocked:
        # The user owns this one. Skipping is the whole point of the flag.
        logger.info('Skipped locked code article %s', module_slug(target['path']))
        return None


def run_code_wiki(repo_id: str, limit: int | None = None, cancel=None) -> dict:
    """Write up to `limit` module articles for one repo.

    `since_sha` is the snapshot the previous pass judged against, so "changed"
    means "changed since we last looked", not "changed in the last commit".
    """
    from backend.repos import registry
    from backend.research import agent

    repo = registry.get_repo(repo_id)
    if not repo or repo.get('cloneState') != 'ready':
        return {'written': [], 'skipped': 0}

    limit = limit if limit is not None else articles_per_night()
    if limit <= 0:
        return {'written': [], 'skipped': 0}

    targets = plan_modules(repo_id, limit, since_sha=_previous_sha(repo_id))
    checkpoint = agent.make_checkpoint(cancel=cancel)

    written, skipped = [], 0
    for target in targets:
        checkpoint()
        try:
            article = write_module_article(repo, target, checkpoint=checkpoint)
        except agent.Cancelled:
            raise
        except Exception as e:
            # One module that fails must not cost the rest of the run.
            logger.warning('Code-wiki pass failed on %s: %s', target['path'], e)
            skipped += 1
            continue
        if article:
            written.append(article['slug'])
        else:
            skipped += 1

    return {'written': written, 'skipped': skipped}


def _previous_sha(repo_id: str) -> str | None:
    """The sha of the snapshot before the current one, for this repo."""
    rows = get_db().execute(
        'SELECT git_sha FROM repo_snapshots WHERE repo_id=?'
        ' ORDER BY generated_at DESC, id DESC LIMIT 2',
        (repo_id,),
    ).fetchall()
    return rows[1]['git_sha'] if len(rows) > 1 else None


def articles_per_night() -> int:
    """How many module articles a nightly pass may write. 0 disables it."""
    try:
        row = get_db().execute(
            'SELECT code_wiki_articles FROM settings LIMIT 1'
        ).fetchone()
    except Exception:
        return DEFAULT_ARTICLES_PER_NIGHT
    if not row or row['code_wiki_articles'] is None:
        return DEFAULT_ARTICLES_PER_NIGHT
    return max(0, int(row['code_wiki_articles']))
