"""Only these columns can reach a mobile replica.

Explicit projections avoid accidentally exporting a future credential or local
file path. New projections change the schema fingerprint and force a bootstrap.
"""
import hashlib
import json

COLLECTIONS = {
    'journal_entries': 'id content raw_content title tags latitude longitude created_at updated_at',
    'journal_attachments': 'id entry_id kind name mime size position source_url import_status transcript transcript_status description description_status created_at',
    'fics': 'id title author source_type source_url description word_count chapter_count download_status last_read_chapter_id rating review last_opened_at created_at updated_at',
    'fic_chapters': 'id fic_id position title category content_html content_text source_url word_count posted_at edited_at created_at updated_at',
    'fic_folders': 'id name created_at',
    'fic_bookmarks': 'id fic_id chapter_id type scroll_position created_at',
    'papers': 'id title archive_requested_at content_updated_at created_at updated_at',
    'paper_pages': 'id paper_id position strokes width height created_at updated_at',
    'paper_page_images': 'id page_id x y width height rotation flipped locked position created_at updated_at',
    'study_sources': 'id title kind source_url content_type size_bytes duration_seconds import_status last_opened_at position paper_id note_mode archive_requested_at created_at updated_at',
    'newspaper_issues': 'id date byte_size page_count markup revision last_read_at created_at',
    'newspaper_frontpages': 'id paper date source_url created_at',
    'wiki_articles': 'id repo_id slug title summary content sources tags kind revision locked created_at updated_at',
    'knowledge_archives': 'id zim_uuid filename title language zim_date flavour size article_count kind enabled has_fulltext_index has_title_index health created_at updated_at',
    'conversations': 'id title created_at updated_at',
    'messages': 'id conversation_id role content status raw_content created_at',
}
COLLECTIONS = {name: tuple(columns.split()) for name, columns in COLLECTIONS.items()}
SCHEMA_HASH = hashlib.sha256(json.dumps(COLLECTIONS, sort_keys=True).encode()).hexdigest()


def included(table: str, prefix: str) -> str:
    # A streaming token must not copy an ever-growing reply into history on
    # every chunk. Publish a message when it is complete or has failed instead.
    return f"{prefix}.status != 'streaming'" if table == 'messages' else '1'
