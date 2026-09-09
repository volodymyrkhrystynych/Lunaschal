// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { parsePracticeSteps, stepIsComplete } from './piano';
import { renderMusicXml } from './verovio';
import { buildFallingNotes } from './pianoVisualization';

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
    expect(steps).toMatchObject([
      { measure: 1, beat: 1, right: [60], left: [48], durationBeats: 1 },
      { measure: 1, beat: 2, right: [64, 67], left: [], durationBeats: 1 },
    ]);
  });

  it('orders interleaved voices by onset and preserves individual held durations', () => {
    const xml = SCORE.replace(
      '<step>C</step><octave>4</octave></pitch><duration>2',
      '<step>C</step><octave>4</octave></pitch><duration>4'
    )
      .replace('<backup><duration>4', '<backup><duration>6')
      .replace(
        '<step>C</step><octave>3</octave></pitch><duration>4',
        '<step>C</step><octave>3</octave></pitch><duration>2'
      )
      .replace(
        '</measure>',
        '<note><pitch><step>D</step><octave>3</octave></pitch><duration>2</duration><staff>2</staff></note></measure>'
      );
    expect(parsePracticeSteps(xml)).toMatchObject([
      {
        beat: 1,
        right: [60],
        left: [48],
        durationBeats: 1,
        noteDurations: { right: [2], left: [1] },
      },
      { beat: 2, right: [], left: [50], durationBeats: 1 },
      { beat: 3, right: [64, 67], left: [] },
    ]);
  });

  it.each(['tie', 'tied'])(
    'merges a %s chain across measures without another attack',
    tag => {
      const tie = (type: string) =>
        tag === 'tie'
          ? `<tie type="${type}"/>`
          : `<notations><tied type="${type}"/></notations>`;
      const note = (marks: string) =>
        `<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration>${marks}</note>`;
      const xml = `<score-partwise><part id="P1"><measure number="1"><attributes><divisions>2</divisions></attributes>${note(tie('start'))}</measure><measure number="2">${note(tie('stop') + tie('start'))}</measure><measure number="3">${note(tie('stop'))}${note('')}</measure></part></score-partwise>`;
      expect(parsePracticeSteps(xml)).toMatchObject([
        {
          measure: 1,
          right: [60],
          durationBeats: 3,
          noteDurations: { right: [3] },
        },
        { measure: 3, beat: 2, right: [60], durationBeats: 1 },
      ]);
    }
  );

  it('requires every selected-hand chord note', () => {
    const step = parsePracticeSteps(SCORE)[1];
    expect(stepIsComplete(step, 'right', new Set([64]))).toBe(false);
    expect(stepIsComplete(step, 'right', new Set([64, 67]))).toBe(true);
  });

  it('keeps a held bass note visible while the melody advances', () => {
    const notes = buildFallingNotes(parsePracticeSteps(SCORE), 1, 'both');
    expect(notes.find(note => note.note === 48)).toMatchObject({
      beatOffset: -1,
      durationBeats: 2,
    });
    expect(notes.some(note => note.note === 60)).toBe(false);
    expect(notes.find(note => note.note === 64)).toMatchObject({
      beatOffset: 0,
      durationBeats: 1,
    });
  });

  it('engraves imported MusicXML as SVG', async () => {
    const pages = await renderMusicXml(SCORE);
    expect(pages[0]).toContain('<svg');
    expect(pages[0]).toContain('class="note"');
  });
});
