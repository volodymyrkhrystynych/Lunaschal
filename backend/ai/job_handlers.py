"""What each `llm_jobs.kind` actually runs.

One module listing every kind of background model work, so a job row can be
executed by a process that knows nothing about the request that created it.
That is the whole reason this file exists: a closure cannot be written to
SQLite, so the queue stores a name and arguments, and the mapping from name
back to code lives here.

Every handler takes `(target_id, payload)` and delegates to the function that
already owned the work — none of the feature logic moved. Those functions grew
a `now=True` branch instead: called normally they queue, called by the worker
they do the thing. Keeping both halves in one function is deliberate, because
the alternative is a pair that can drift, and the failure mode of drift here is
silent (work queued under a name whose handler does something subtly else).

Imports are inside the handlers. These modules are Flask blueprints that import
half the app, and importing them at module scope would make the job queue a
participant in the app's import graph rather than a leaf of it.
"""
from backend.ai import jobs


@jobs.handler('journal.polish')
def _journal_polish(target_id, payload):
    from backend.routes.journal import _polish_bg
    _polish_bg(target_id, payload['raw_content'], now=True)


@jobs.handler('journal.metadata')
def _journal_metadata(target_id, payload):
    from backend.routes.journal import _generate_metadata_bg
    _generate_metadata_bg(target_id, payload['content'], now=True)


@jobs.handler('journal.transcribe_attachment')
def _journal_transcribe(target_id, payload):
    from backend.routes.journal import _transcribe_attachment_bg
    _transcribe_attachment_bg(
        target_id, payload['entry_id'], payload['kind'], payload['path'],
        payload['name'], into_entry=payload.get('into_entry', False), now=True,
    )


@jobs.handler('journal.describe_audio')
def _journal_describe_audio(target_id, payload):
    from backend.routes.journal import _describe_attachment_bg
    _describe_attachment_bg(target_id, payload['entry_id'], payload['path'],
                            payload['name'], now=True)


@jobs.handler('chat.read_attachment')
def _chat_read_attachment(target_id, payload):
    from backend.routes.chat import _read_attachment_bg
    _read_attachment_bg(target_id, payload['path'], now=True)


@jobs.handler('chat.compaction')
def _chat_compaction(target_id, payload):
    from backend.chat.compaction import run_pending
    run_pending(target_id)


@jobs.handler('food.structure')
def _food_structure(target_id, payload):
    from backend.routes.food import structure_food_entry
    structure_food_entry(target_id, payload['text'])


@jobs.handler('food.transcribe_media')
def _food_transcribe_media(target_id, payload):
    from backend.routes.food import _transcribe_media_bg
    _transcribe_media_bg(target_id, payload['entry_id'], payload['path'], now=True)


@jobs.handler('food.describe_media')
def _food_describe_media(target_id, payload):
    from backend.routes.food import describe_food_media
    describe_food_media(target_id)


@jobs.handler('food.recipe_match')
def _food_recipe_match(target_id, payload):
    from backend.food.recipe_match import check_homemade_recipe_match
    check_homemade_recipe_match(target_id)


@jobs.handler('lifestyle.structure_workout')
def _lifestyle_structure_workout(target_id, payload):
    from backend.routes.lifestyle import structure_workout
    structure_workout(target_id, payload['text'])


@jobs.handler('learning.grade_attempt')
def _learning_grade_attempt(target_id, payload):
    from backend.routes.learning import _queue_grade
    _queue_grade(target_id, now=True)


@jobs.handler('calendar.classify')
def _calendar_classify(target_id, payload):
    from backend.ai.calendar import classify_event_categories
    classify_event_categories(target_id)


@jobs.handler('ideas.enrich')
def _ideas_enrich(target_id, payload):
    from backend.routes.ideas import _enrich_idea_bg
    _enrich_idea_bg(target_id, payload['raw_content'],
                    polish=payload.get('polish', True), now=True)


@jobs.handler('email.classify')
def _email_classify(target_id, payload):
    from backend.ai.email import classify_email
    classify_email(target_id)
