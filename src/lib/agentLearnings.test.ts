import { describe, expect, it } from 'vitest';
import doc from '../../docs/agent-learnings.md?raw';
import { AGENT_LEARNINGS_SNIPPET, describeImport } from './agentLearnings';

describe('AGENT_LEARNINGS_SNIPPET', () => {
  it('is the same text docs/agent-learnings.md tells you to paste', () => {
    expect(doc).toContain(AGENT_LEARNINGS_SNIPPET.trim());
  });

  it('asks for the file shape the importer parses', () => {
    expect(AGENT_LEARNINGS_SNIPPET).toContain('~/learnings/YYYY-MM-DD.md');
    expect(AGENT_LEARNINGS_SNIPPET).toContain('## <the principle');
  });
});

describe('describeImport', () => {
  it('summarises an upload', () => {
    expect(describeImport({ added: 1, alreadyImported: 0 })).toBe(
      '1 new lesson'
    );
    expect(describeImport({ added: 2, alreadyImported: 3 })).toBe(
      '2 new lessons · 3 already imported'
    );
  });

  it('summarises an inbox scan', () => {
    expect(describeImport({ added: 0, alreadyImported: 4, files: 2 })).toBe(
      '2 files read · 0 new lessons · 4 already imported'
    );
  });
});
