import { useEffect, useRef } from 'react';
import { EditorView, basicSetup } from 'codemirror';
import { EditorState, StateEffect, StateField } from '@codemirror/state';
import { Decoration, type DecorationSet, WidgetType } from '@codemirror/view';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { oneDark } from '@codemirror/theme-one-dark';
import type { CharStatus } from '../../lib/practice';

function getLang(language: string) {
  if (language === 'html') return html();
  if (language === 'css') return css();
  return javascript({ jsx: language === 'react' });
}

class CaretWidget extends WidgetType {
  toDOM() {
    const span = document.createElement('span');
    span.className = 'practice-caret';
    return span;
  }
  eq() {
    return true;
  }
}

const setDiff = StateEffect.define<CharStatus[]>();

function buildDecorations(statuses: CharStatus[]): DecorationSet {
  const marks = statuses
    .map((status, i) =>
      status === 'pending'
        ? null
        : Decoration.mark({
            class:
              status === 'correct'
                ? 'practice-char-correct'
                : 'practice-char-incorrect',
          }).range(i, i + 1)
    )
    .filter((m): m is NonNullable<typeof m> => m !== null);
  const firstPending = statuses.findIndex(s => s === 'pending');
  const caretPos = firstPending === -1 ? statuses.length : firstPending;
  marks.push(
    Decoration.widget({ widget: new CaretWidget(), side: -1 }).range(caretPos)
  );
  return Decoration.set(marks, true);
}

const diffField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    for (const e of tr.effects) {
      if (e.is(setDiff)) return buildDecorations(e.value);
    }
    return deco;
  },
  provide: f => EditorView.decorations.from(f),
});

interface Props {
  language: string;
  code: string;
  statuses: CharStatus[];
}

// Read-only, syntax-highlighted display of the target snippet — deliberately
// not the surface that captures keystrokes (DrillSession's hidden <input>
// does that). CodeMirror here only renders the code plus the correctness
// overlay pushed in via `setDiff`.
export function TypingCanvas({ language, code, statuses }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    viewRef.current?.destroy();

    const view = new EditorView({
      state: EditorState.create({
        doc: code,
        extensions: [
          basicSetup,
          oneDark,
          getLang(language),
          EditorView.editable.of(false),
          EditorState.readOnly.of(true),
          diffField,
          EditorView.theme({
            '&': { fontSize: '15px' },
            '.cm-scroller': { fontFamily: 'var(--font-mono, monospace)' },
            '.cm-content': { caretColor: 'transparent' },
          }),
        ],
      }),
      parent: containerRef.current,
    });
    viewRef.current = view;

    return () => view.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, language]);

  useEffect(() => {
    viewRef.current?.dispatch({ effects: setDiff.of(statuses) });
  }, [statuses]);

  return (
    <div
      ref={containerRef}
      className="rounded-lg border border-white/10 overflow-hidden"
    />
  );
}
