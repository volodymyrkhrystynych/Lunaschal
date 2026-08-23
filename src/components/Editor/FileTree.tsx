import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcuts } from '../../shortcuts/ShortcutProvider';
import { useFileTree } from '../../hooks/useFileTree';
import { FileTreeRow } from '../FileTreeRow';

interface Props {
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
}

export function FileTree({ selectedPath, onSelectFile }: Props) {
  const { level } = useShortcuts();
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOverRoot, setDragOverRoot] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: ({ dir, files }: { dir: string; files: File[] }) =>
      api.files.upload(dir, files),
    onSuccess: result => {
      qc.invalidateQueries({ queryKey: ['files', 'list'] });
      setUploadError(
        result.errors.length
          ? `Could not upload: ${result.errors.map(e => e.name).join(', ')}`
          : null
      );
    },
    onError: (err: Error) => setUploadError(err.message || 'Upload failed'),
  });

  const uploadFiles = (dir: string, files: File[]) => {
    if (files.length) upload.mutate({ dir, files });
  };

  const {
    visibleNodes,
    rootEntries,
    expandedDirs,
    toggleDir,
    focusedIdx,
    newFileName,
    setNewFileName,
    showNewFile,
    setShowNewFile,
    newFolderName,
    setNewFolderName,
    showNewFolder,
    setShowNewFolder,
    renamingEntry,
    setRenamingEntry,
    renameValue,
    setRenameValue,
    createFile,
    createFolder,
    handleDelete,
    handleRenameStart,
    handleRenameConfirm,
  } = useFileTree({
    api: api.files,
    queryKeyPrefix: ['files'],
    selectedPath,
    onSelectFile,
  });

  return (
    <div className="flex flex-col h-full">
      <div className="p-2 border-b border-white/10 flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Files
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowNewFolder(true)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
            title="New folder"
          >
            + Folder
          </button>
          <button
            onClick={() => setShowNewFile(true)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
            title="New file"
          >
            + New
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
            title="Upload files"
          >
            ⬆ Upload
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={e => {
              const files = Array.from(e.target.files ?? []);
              uploadFiles('', files);
              e.target.value = '';
            }}
          />
        </div>
      </div>

      <div
        className={`flex-1 overflow-y-auto py-1 ${dragOverRoot ? 'bg-[var(--color-primary)]/5 ring-1 ring-inset ring-[var(--color-primary)]' : ''}`}
        onDragOver={e => {
          e.preventDefault();
          setDragOverRoot(true);
        }}
        onDragLeave={() => setDragOverRoot(false)}
        onDrop={e => {
          e.preventDefault();
          setDragOverRoot(false);
          uploadFiles('', Array.from(e.dataTransfer.files));
        }}
      >
        {upload.isPending && (
          <div className="px-4 py-1 text-xs text-[var(--color-text-muted)]">
            Uploading…
          </div>
        )}
        {uploadError && (
          <div className="px-4 py-1 text-xs text-red-400">{uploadError}</div>
        )}
        {showNewFile && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={newFileName}
              onChange={e => setNewFileName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFileName.trim())
                  createFile.mutate(newFileName.trim());
                if (e.key === 'Escape') {
                  setShowNewFile(false);
                  setNewFileName('');
                }
              }}
              onBlur={() => {
                setShowNewFile(false);
                setNewFileName('');
              }}
              placeholder="filename.md"
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {showNewFolder && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFolderName.trim())
                  createFolder.mutate(newFolderName.trim());
                if (e.key === 'Escape') {
                  setShowNewFolder(false);
                  setNewFolderName('');
                }
              }}
              onBlur={() => {
                setShowNewFolder(false);
                setNewFolderName('');
              }}
              placeholder="folder name"
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {renamingEntry && (
          <div className="px-2 py-1">
            <input
              autoFocus
              value={renameValue}
              onChange={e => setRenameValue(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleRenameConfirm();
                if (e.key === 'Escape') setRenamingEntry(null);
              }}
              onBlur={() => setRenamingEntry(null)}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-primary)] rounded px-2 py-0.5 text-sm text-[var(--color-text)] focus:outline-none"
            />
          </div>
        )}

        {visibleNodes.map((node, idx) => (
          <FileTreeRow
            key={node.entry.path}
            entry={node.entry}
            depth={node.depth}
            isExpanded={expandedDirs.has(node.entry.path)}
            isSelected={selectedPath === node.entry.path}
            isFocused={level >= 1 && idx === focusedIdx}
            onToggleDir={toggleDir}
            onSelectFile={onSelectFile}
            onDelete={handleDelete}
            onRenameStart={handleRenameStart}
            onDropFiles={uploadFiles}
          />
        ))}

        {rootEntries?.length === 0 && !showNewFile && (
          <div className="px-4 py-4 text-xs text-[var(--color-text-muted)]">
            No files yet. Click "+ New", "Upload", or drop files here.
          </div>
        )}
      </div>
    </div>
  );
}
