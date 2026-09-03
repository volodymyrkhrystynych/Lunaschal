// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { parsePracticeSteps, stepIsComplete } from './piano';
import { renderMusicXml } from './verovio';

const SCORE = `<score-partwise><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list><part id="P1"><measure number="1">
  <attributes><divisions>2</divisions></attributes>
  <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><staff>1</staff></note>
  <note><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration><staff>1</staff></note>
  <note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration><staff>1</staff></note>
  <backup><duration>4</duration></backup>
  <note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><staff>2</staff></note>
</measure></part></score-partwise>`;

describe('MusicXML practice timeline', () => {
  it('groups chords and aligns both staves by musical onset', () => {
    const steps = parsePracticeSteps(SCORE);
    expect(steps).toEqual([
      { measure: 1, beat: 1, right: [60], left: [48], durationBeats: 2 },
      { measure: 1, beat: 2, right: [64, 67], left: [], durationBeats: 1 },
    ]);
  });

  it('requires every selected-hand chord note', () => {
    const step = parsePracticeSteps(SCORE)[1];
    expect(stepIsComplete(step, 'right', new Set([64]))).toBe(false);
    expect(stepIsComplete(step, 'right', new Set([64, 67]))).toBe(true);
  });

  it('engraves imported MusicXML as SVG', async () => {
    const pages = await renderMusicXml(SCORE);
    expect(pages[0]).toContain('<svg');
    expect(pages[0]).toContain('class="note"');
  });
});
