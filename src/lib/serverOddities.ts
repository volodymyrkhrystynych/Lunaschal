/** Read the saved briefing section, without fetching today's mutable report. */
export function splitServerOddities(content: string, metadata?: string | null) {
  try {
    if (JSON.parse(metadata ?? '{}')?.briefing !== true) return null;
  } catch {
    return null;
  }
  const lines = content.split('\n');
  const start = lines.indexOf('**Server oddities (past day)**');
  if (start < 0) return null;
  const items: { title: string; note: string }[] = [];
  const notices: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith('- **')) {
      items.push({ title: line.slice(2), note: '' });
    } else if (line.startsWith('  ') && items.length) {
      const item = items[items.length - 1];
      item.note += (item.note ? '\n' : '') + line.trimStart();
    } else {
      notices.push(line);
    }
  }
  return {
    prose: lines.slice(0, start).join('\n').trimEnd(),
    items,
    notices: notices.join('\n').trim(),
  };
}
