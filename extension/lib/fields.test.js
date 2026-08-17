// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import {
  classify,
  collectFields,
  deriveLabel,
  findResumeInputs,
  humanizeName,
  isFillable,
  optionsFor,
} from './fields.js';

/**
 * The markup shapes here are the ones real ATS forms use, not invented ones:
 * a plain `<label for>` (Greenhouse), a wrapping label (Lever), `aria-label`
 * with no label element (Ashby), and a `<div>` above the input with no label
 * at all (Workday). Getting the label wrong means the model answers a question
 * nobody asked and the answer is filed under it.
 */

function mount(html) {
  document.body.innerHTML = html;
  return document.body;
}

const q = selector => document.querySelector(selector);

beforeEach(() => {
  document.body.innerHTML = '';
});

// --------------------------------------------------------------------------
// deriveLabel
// --------------------------------------------------------------------------

describe('deriveLabel', () => {
  it('reads an explicit label[for]', () => {
    mount('<label for="e">Email address</label><input id="e" name="email">');
    expect(deriveLabel(q('#e'))).toBe('Email address');
  });

  it('reads a wrapping label', () => {
    mount('<label>Full name <input name="n"></label>');
    expect(deriveLabel(q('input'))).toBe('Full name');
  });

  it('reads aria-label', () => {
    mount('<input aria-label="Years of experience" name="yoe">');
    expect(deriveLabel(q('input'))).toBe('Years of experience');
  });

  it('reads aria-labelledby, joining the referenced nodes', () => {
    mount(`
      <span id="a">Why do you want</span><span id="b">to work here?</span>
      <textarea aria-labelledby="a b"></textarea>
    `);
    expect(deriveLabel(q('textarea'))).toBe('Why do you want to work here?');
  });

  it('falls back to preceding text when there is no label element', () => {
    mount('<div class="q">Notice period</div><div><input name="np"></div>');
    expect(deriveLabel(q('input'))).toBe('Notice period');
  });

  it('falls back to the placeholder', () => {
    mount('<input placeholder="LinkedIn URL" name="li">');
    expect(deriveLabel(q('input'))).toBe('LinkedIn URL');
  });

  it('falls back to a humanized name attribute', () => {
    mount('<input name="first_name">');
    expect(deriveLabel(q('input'))).toBe('First name');
  });

  it('returns empty when there is nothing to go on', () => {
    mount('<input>');
    expect(deriveLabel(q('input'))).toBe('');
  });

  it('prefers aria-label over a nearby placeholder', () => {
    mount('<input aria-label="Salary expectation" placeholder="e.g. 120000">');
    expect(deriveLabel(q('input'))).toBe('Salary expectation');
  });

  it('strips the required marker so one question is not recorded twice', () => {
    mount('<label for="e">Email *</label><input id="e">');
    expect(deriveLabel(q('#e'))).toBe('Email');
  });

  it('strips a trailing colon', () => {
    mount('<label for="e">Phone:</label><input id="e">');
    expect(deriveLabel(q('#e'))).toBe('Phone');
  });

  it('does not swallow the control own text from a wrapping label', () => {
    mount(`
      <label>Work authorization
        <select name="wa"><option>Yes, I am authorized</option></select>
      </label>
    `);
    expect(deriveLabel(q('select'))).toBe('Work authorization');
  });

  it('does not borrow a label belonging to another control', () => {
    mount(`
      <div><label for="a">First</label><input id="a"></div>
      <div><input name="second"></div>
    `);
    // The preceding sibling contains its own input, so it must be skipped.
    expect(deriveLabel(q('[name="second"]'))).toBe('Second');
  });

  it('survives an id containing characters that break a selector', () => {
    mount('<label for="a.b:c">Odd id</label><input id="a.b:c">');
    expect(deriveLabel(q('input'))).toBe('Odd id');
  });
});

// --------------------------------------------------------------------------
// classify / optionsFor
// --------------------------------------------------------------------------

describe('classify', () => {
  it('recognises a textarea', () => {
    mount('<textarea aria-label="Cover letter"></textarea>');
    expect(classify(q('textarea'))).toBe('textarea');
  });

  it('recognises a number input', () => {
    mount('<input type="number" aria-label="Years">');
    expect(classify(q('input'))).toBe('number');
  });

  it('recognises a checkbox as boolean', () => {
    mount('<input type="checkbox" aria-label="Willing to relocate">');
    expect(classify(q('input'))).toBe('boolean');
  });

  it('treats a yes/no select as boolean rather than a dropdown', () => {
    mount(`
      <select aria-label="Authorized?">
        <option>Yes</option><option>No</option>
      </select>
    `);
    expect(classify(q('select'))).toBe('boolean');
  });

  it('treats a real dropdown as a select', () => {
    mount(`
      <select aria-label="Country">
        <option>Canada</option><option>United States</option><option>Other</option>
      </select>
    `);
    expect(classify(q('select'))).toBe('select');
  });

  it('lists a select options', () => {
    mount(
      '<select aria-label="C"><option>Canada</option><option>Peru</option></select>'
    );
    expect(optionsFor(q('select'))).toEqual(['Canada', 'Peru']);
  });

  it('lists a radio group options from its labels', () => {
    mount(`
      <form>
        <label><input type="radio" name="r" value="y"> Yes</label>
        <label><input type="radio" name="r" value="n"> No</label>
      </form>
    `);
    expect(optionsFor(q('input[type="radio"]'))).toEqual(['Yes', 'No']);
  });
});

// --------------------------------------------------------------------------
// isFillable
// --------------------------------------------------------------------------

describe('isFillable', () => {
  it('accepts an ordinary text input', () => {
    mount('<input name="a">');
    expect(isFillable(q('input'))).toBe(true);
  });

  it.each(['hidden', 'submit', 'button', 'reset', 'image', 'file'])(
    'skips type=%s',
    type => {
      mount(`<input type="${type}" name="a">`);
      expect(isFillable(q('input'))).toBe(false);
    }
  );

  it('never fills a password field', () => {
    mount('<input type="password" name="p">');
    expect(isFillable(q('input'))).toBe(false);
  });

  it('skips disabled and readonly controls', () => {
    mount('<input name="a" disabled><input name="b" readonly>');
    expect(isFillable(q('[name="a"]'))).toBe(false);
    expect(isFillable(q('[name="b"]'))).toBe(false);
  });

  it('skips a display:none field', () => {
    mount('<input name="a" style="display:none">');
    expect(isFillable(q('input'))).toBe(false);
  });

  it('skips a field inside a hidden container', () => {
    mount('<div hidden><input name="a"></div>');
    expect(isFillable(q('input'))).toBe(false);
  });

  it('skips a honeypot, because filling one bins the application', () => {
    mount('<input name="honeypot_email"><input name="b" class="bot-field">');
    expect(isFillable(q('[name="honeypot_email"]'))).toBe(false);
    expect(isFillable(q('[name="b"]'))).toBe(false);
  });
});

// --------------------------------------------------------------------------
// collectFields
// --------------------------------------------------------------------------

describe('collectFields', () => {
  it('collects a whole form with labels and types', () => {
    mount(`
      <form>
        <label for="n">Full name</label><input id="n" name="name">
        <label for="c">Cover letter</label><textarea id="c"></textarea>
        <input type="hidden" name="csrf" value="x">
        <input type="submit" value="Apply">
      </form>
    `);
    const fields = collectFields(document);
    expect(fields.map(f => [f.label, f.type])).toEqual([
      ['Full name', 'text'],
      ['Cover letter', 'textarea'],
    ]);
  });

  it('collapses a radio group into one question', () => {
    mount(`
      <form>
        <div>Are you authorized to work?</div>
        <label><input type="radio" name="auth" value="y"> Yes</label>
        <label><input type="radio" name="auth" value="n"> No</label>
      </form>
    `);
    const fields = collectFields(document);
    expect(fields).toHaveLength(1);
    expect(fields[0].options).toEqual(['Yes', 'No']);
  });

  it('drops fields with no derivable label rather than asking about ""', () => {
    mount('<form><input><input aria-label="Real"></form>');
    expect(collectFields(document).map(f => f.label)).toEqual(['Real']);
  });

  it('returns the elements so the caller can fill them', () => {
    mount('<input aria-label="Email" name="email">');
    expect(collectFields(document)[0].element).toBe(q('input'));
  });
});

// --------------------------------------------------------------------------
// findResumeInputs
// --------------------------------------------------------------------------

describe('findResumeInputs', () => {
  it('prefers the input that mentions a resume', () => {
    mount(`
      <input type="file" name="photo">
      <input type="file" name="resume_upload">
    `);
    expect(findResumeInputs(document)[0].getAttribute('name')).toBe(
      'resume_upload'
    );
  });

  it('falls back to a document-accepting input', () => {
    mount(`
      <input type="file" name="photo" accept="image/*">
      <input type="file" name="attachment" accept=".pdf,.docx">
    `);
    expect(findResumeInputs(document)[0].getAttribute('name')).toBe(
      'attachment'
    );
  });

  it('ignores disabled inputs', () => {
    mount('<input type="file" name="resume" disabled>');
    expect(findResumeInputs(document)).toEqual([]);
  });
});

describe('humanizeName', () => {
  it.each([
    ['first_name', 'First name'],
    ['firstName', 'First name'],
    ['first-name', 'First name'],
    ['', ''],
  ])('%s -> %s', (input, expected) => {
    expect(humanizeName(input)).toBe(expected);
  });
});
