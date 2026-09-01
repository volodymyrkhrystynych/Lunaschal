from backend.storage import IdScopedStorage

_storage = IdScopedStorage('PIANO_ROOT', './data/piano')

piano_dir = _storage.dir
delete_piano_dir = _storage.delete_dir
resolve_stored_path = _storage.resolve_stored_path
