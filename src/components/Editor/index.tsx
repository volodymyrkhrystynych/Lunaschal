import { useState } from 'react';
import { FileTree } from './FileTree';
import { EditorPane } from './EditorPane';

interface Props {
  pendingInsert: string | null;
  onInsertDone: () => void;
}

export function Editor({ pendingInsert, onInsertDone }: Props) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  return (
    <div className="flex-1 flex overflow-hidden">
      <div className="w-56 shrink-0 border-r border-white/10 bg-[var(--color-surface)] overflow-hidden flex flex-col">
        <FileTree selectedPath={selectedPath} onSelectFile={setSelectedPath} />
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedPath ? (
          <EditorPane
            filePath={selectedPath}
            pendingInsert={pendingInsert}
            onInsertDone={onInsertDone}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
            Select a file to edit
          </div>
        )}
      </div>
    </div>
  );
}
