"""Only these columns can reach a mobile replica.

Explicit projections avoid accidentally exporting a future credential or local
file path. New projections change the schema fingerprint and force a bootstrap.
"""
import hashlib
import json

from backend.fanfic.source_lists import backlog_sql

COLLECTIONS = {
    'journal_entries': 'id content raw_content title tags latitude longitude weather created_at updated_at',
    # `has_thumb`/`has_motion` are derived below: the phone needs to know a poster
    # or a Live Photo clip exists without ever learning where it is stored.
    'journal_attachments': 'id entry_id kind name mime size position source_url import_status transcript transcript_status description description_status duration_seconds captured_at motion_mime created_at',
    'fics': 'id title author source_type source_url site description word_count chapter_count download_status last_read_chapter_id rating review shelf shelved_at last_opened_at created_at updated_at',
    'fic_chapters': 'id fic_id position title category content_html content_text source_url word_count posted_at edited_at created_at updated_at',
    'fic_folders': 'id name position created_at',
    'fic_bookmarks': 'id fic_id chapter_id type scroll_position created_at',
    # Keyed by chapter: one row per chapter marked read, and one per chapter
    # whose post the forum showed the user's own reaction on (or none).
    'fic_chapter_reads': 'chapter_id fic_id created_at',
    'fic_chapter_reactions': 'chapter_id fic_id reaction checked_at source pending push_error',
    'papers': 'id title archive_requested_at content_updated_at created_at updated_at',
    'paper_pages': 'id paper_id position strokes width height created_at updated_at',
    'paper_native_ink': 'id format sha256 preview_sha256 created_at updated_at',
    'paper_page_images': 'id page_id x y width height rotation flipped locked position created_at updated_at',
    'study_sources': 'id title kind source_url content_type size_bytes duration_seconds import_status last_opened_at position paper_id note_mode archive_requested_at created_at updated_at',
    'newspaper_issues': 'id date byte_size page_count markup revision last_read_at created_at',
    'newspaper_frontpages': 'id paper date source_url created_at',
    'wiki_articles': 'id repo_id slug title summary content sources tags kind revision locked created_at updated_at',
    'knowledge_archives': 'id zim_uuid filename title language zim_date flavour size article_count kind enabled has_fulltext_index has_title_index health created_at updated_at',
    'conversations': 'id title created_at updated_at',
    'messages': 'id conversation_id role content status raw_content created_at',
    # Read-only on the device: the phone expands the series itself, so it gets
    # the template rows and their per-occurrence exceptions, not instances.
    'calendar_events': 'id title description date time end_time all_day tags category_tags repeat_freq repeat_interval repeat_byweekday repeat_until created_at',
    'calendar_event_exceptions': 'id event_id date action new_date new_time new_end_time created_at',
    # The food log, so a meal logged anywhere can be read and added to on the
    # phone. `generated_notes` and the weather check time are server bookkeeping.
    'food_entries': 'id raw_content dish place notes rating tags latitude longitude weather created_at updated_at',
    'food_media': 'id entry_id kind mime position transcript transcript_status description description_status created_at',
    # Purchases, typed or captured as a receipt, for the phone's Journal feed.
    'spending_logs': 'id date category amount_cents merchant raw_content notes latitude longitude created_at updated_at',
    'spending_media': 'id entry_id kind mime position transcript transcript_status description description_status created_at',
}
COLLECTIONS = {name: tuple(columns.split()) for name, columns in COLLECTIONS.items()}
# The column a collection's record id comes from, where it is not `id`.
KEYS = {'fic_chapter_reads': 'chapter_id', 'fic_chapter_reactions': 'chapter_id'}


def key(table: str) -> str:
    return KEYS.get(table, 'id')
SCHEMA_HASH = hashlib.sha256(json.dumps([COLLECTIONS, KEYS, 'book-metadata-v4'], sort_keys=True).encode()).hexdigest()


def payload(table: str, prefix: str) -> str:
    fields = ','.join(f"'{c}',{prefix}.\"{c}\"" for c in COLLECTIONS[table])
    if table in KEYS:
        # Every replica record carries its own id; the phone checks it.
        fields = f"'id',{prefix}.\"{KEYS[table]}\"," + fields
    if table == 'fics':
        fields += f""",
            'folder_ids',json((SELECT json_group_array(folder_id) FROM
                (SELECT folder_id FROM fic_folder_items WHERE fic_id={prefix}.id ORDER BY folder_id))),
            'tags',json((SELECT json_group_array(name) FROM
                (SELECT name FROM fic_site_tags WHERE fic_id={prefix}.id ORDER BY name))),
            'unread_count',(SELECT COUNT(*) FROM fic_chapters c WHERE c.fic_id={prefix}.id
                AND lower(c.category)='threadmarks'
                AND NOT EXISTS (SELECT 1 FROM fic_chapter_reads r WHERE r.chapter_id=c.id)),
            'source_lists',json((SELECT json_group_array(list) FROM
                (SELECT list FROM fic_source_lists WHERE fic_id={prefix}.id ORDER BY list))),
            'backlog',{backlog_sql(prefix)},
            'latest_activity',COALESCE(
                (SELECT MAX(posted_at) FROM fic_chapters WHERE fic_id={prefix}.id),
                (SELECT MAX(created_at) FROM fic_chapters WHERE fic_id={prefix}.id),{prefix}.created_at)
        """
    if table == 'journal_attachments':
        fields += f""",
            'has_thumb',({prefix}.thumb_path IS NOT NULL AND {prefix}.thumb_path != ''),
            'has_motion',({prefix}.motion_path IS NOT NULL AND {prefix}.motion_path != '')
        """
    return f'json_object({fields})'


def included(table: str, prefix: str) -> str:
    # A streaming token must not copy an ever-growing reply into history on
    # every chunk. Publish a message when it is complete or has failed instead.
    return f"{prefix}.status != 'streaming'" if table == 'messages' else '1'
