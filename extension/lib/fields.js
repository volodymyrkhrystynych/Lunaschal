/**
 * Reading a job-application form: which controls to fill, and what each one is
 * actually asking.
 *
 * This is the whole risk of the extension. Everything else is plumbing — if
 * the label is wrong, the model answers a question nobody asked and the answer
 * is recorded against it. So label derivation is ordered by how *deliberate*
 * each source is: an author who wrote `aria-label` meant it, while a
 * `placeholder` is often "e.g. Jane Smith" and a `name` is whatever the
 * backend column was called.
 *
 * A pure ES module with no `chrome.*` and no network, so Vitest can drive it
 * against jsdom fixtures. The content script pulls it in with a dynamic
 * import; nothing here may reach for extension APIs.
 */

/** Controls we never touch, whatever their label says. */
const SKIPPED_INPUT_TYPES = new Set([
  'hidden',
  'submit',
  'reset',
  'button',
  'image',
  'file', // handled separately — that is the resume upload path
  'password', // never autofill a credential
]);

/** Name/id fragments that mean "this field exists to catch bots". Filling one
 * is how a real application gets silently binned. */
const HONEYPOT_HINTS = [
  'honeypot',
  'honey-pot',
  'bot-field',
  'botfield',
  'nobot',
  '_gotcha',
  'leaveblank',
  'leave-blank',
];

const BOOLEAN_WORDS = /\b(yes|no|true|false)\b/i;

function textOf(node) {
  return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function clean(value) {
  if (typeof value !== 'string') return '';
  // Strip the decorations forms put on required fields; "Email *" and "Email"
  // are the same question and must not become two recorded answers.
  return value
    .replace(/[ \s]+/g, ' ')
    .replace(/[*✱﹡]/g, '')
    .replace(/\s*\(required\)\s*/i, ' ')
    .replace(/\s*\(optional\)\s*/i, ' ')
    .trim()
    .replace(/\s*:$/, '');
}

function attr(el, name) {
  return clean(el.getAttribute?.(name) ?? '');
}

/**
 * The label a wrapping `<label>` provides, minus any text belonging to the
 * control itself — otherwise a select's chosen option becomes part of its own
 * question.
 */
function wrappingLabelText(el) {
  const label = el.closest?.('label');
  if (!label) return '';
  const clone = label.cloneNode(true);
  for (const control of clone.querySelectorAll(
    'input, select, textarea, button'
  )) {
    control.remove();
  }
  return clean(textOf(clone));
}

function labelledByText(el) {
  const ids = attr(el, 'aria-labelledby');
  if (!ids) return '';
  const doc = el.ownerDocument;
  const parts = ids
    .split(/\s+/)
    .map(id => doc.getElementById(id))
    .filter(Boolean)
    .map(textOf);
  return clean(parts.join(' '));
}

function explicitLabelText(el) {
  if (!el.id) return '';
  const doc = el.ownerDocument;
  // CSS.escape guards ids containing the dots and colons that Workday and
  // friends generate.
  const escaped = globalThis.CSS?.escape ? globalThis.CSS.escape(el.id) : el.id;
  let label = null;
  try {
    label = doc.querySelector(`label[for="${escaped}"]`);
  } catch {
    label = null;
  }
  return label ? clean(textOf(label)) : '';
}

/**
 * The nearest preceding text that reads like a question.
 *
 * Plenty of real forms use a `<div>` above the input and no label element at
 * all. Walks previous siblings, then up one container, and stops at anything
 * containing its own form control — that text belongs to the other control.
 */
function precedingText(el) {
  let node = el;
  for (let depth = 0; depth < 3 && node; depth += 1) {
    let sibling = node.previousElementSibling;
    while (sibling) {
      if (!sibling.querySelector?.('input, select, textarea')) {
        const text = clean(textOf(sibling));
        if (text && text.length <= 200) return text;
      }
      sibling = sibling.previousElementSibling;
    }
    node = node.parentElement;
  }
  return '';
}

/** `firstName` / `first_name` / `first-name` → "First name". */
export function humanizeName(name) {
  const spaced = String(name || '')
    .replace(/[_\-.]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
  if (!spaced) return '';
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/**
 * What this control is asking, or '' when nothing legible could be found.
 *
 * Order is by authorial intent, not convenience: things the author wrote to
 * describe the field beat things that happen to be nearby.
 */
export function deriveLabel(el) {
  if (!el) return '';
  return (
    labelledByText(el) ||
    attr(el, 'aria-label') ||
    explicitLabelText(el) ||
    wrappingLabelText(el) ||
    precedingText(el) ||
    attr(el, 'placeholder') ||
    humanizeName(el.getAttribute?.('name') || '')
  );
}

/** The question type, in the vocabulary `backend/jobs/answers.py` accepts. */
export function classify(el) {
  const tag = el.tagName?.toLowerCase();
  if (tag === 'textarea') return 'textarea';
  if (tag === 'select') {
    // A two-option select of yes/no is a boolean question wearing a dropdown.
    const values = optionsFor(el);
    const isBoolean =
      values.length > 0 &&
      values.length <= 3 &&
      values.every(v => BOOLEAN_WORDS.test(v) || !v);
    return isBoolean ? 'boolean' : 'select';
  }

  const type = (el.getAttribute?.('type') || 'text').toLowerCase();
  if (type === 'checkbox' || type === 'radio') return 'boolean';
  if (type === 'number' || type === 'range') return 'number';
  return 'text';
}

/** The choices a select or radio group offers. Empty for free-text fields. */
export function optionsFor(el) {
  if (el.tagName?.toLowerCase() === 'select') {
    return Array.from(el.options || [])
      .map(o => clean(o.textContent || o.value))
      .filter(Boolean);
  }
  const type = (el.getAttribute?.('type') || '').toLowerCase();
  if (type === 'radio' && el.name) {
    const form = el.form || el.ownerDocument;
    let escaped = el.name;
    if (globalThis.CSS?.escape) escaped = globalThis.CSS.escape(el.name);
    let group = [];
    try {
      group = Array.from(
        form.querySelectorAll(`input[type="radio"][name="${escaped}"]`)
      );
    } catch {
      group = [];
    }
    return group.map(r => deriveLabel(r) || r.value).filter(Boolean);
  }
  return [];
}

function looksLikeHoneypot(el) {
  const haystack = `${el.getAttribute?.('name') || ''} ${el.id || ''} ${
    el.className || ''
  }`.toLowerCase();
  return HONEYPOT_HINTS.some(hint => haystack.includes(hint));
}

function isHidden(el) {
  if (el.hidden) return true;
  if (el.getAttribute?.('aria-hidden') === 'true') return true;

  const inline = el.style;
  if (inline?.display === 'none' || inline?.visibility === 'hidden')
    return true;

  const view = el.ownerDocument?.defaultView;
  if (view?.getComputedStyle) {
    const computed = view.getComputedStyle(el);
    if (computed.display === 'none' || computed.visibility === 'hidden')
      return true;
  }
  // An ancestor may be the thing that is hidden — a collapsed accordion step.
  return Boolean(el.closest?.('[hidden], [aria-hidden="true"]'));
}

/** True when this control is one we should offer to fill. */
export function isFillable(el) {
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  if (!['input', 'select', 'textarea'].includes(tag)) return false;
  if (
    tag === 'input' &&
    SKIPPED_INPUT_TYPES.has((el.getAttribute('type') || 'text').toLowerCase())
  ) {
    return false;
  }
  if (el.disabled || el.readOnly) return false;
  if (looksLikeHoneypot(el)) return false;
  if (isHidden(el)) return false;
  return true;
}

/**
 * Every fillable control under `root`, each with the question it represents.
 *
 * Radio groups collapse to one entry — a group is one question, and treating
 * each button as its own would ask the model the same thing five times.
 * Fields with no derivable label are dropped rather than sent as ''.
 */
export function collectFields(root) {
  const scope = root?.querySelectorAll ? root : root?.ownerDocument;
  if (!scope) return [];

  const seenRadioGroups = new Set();
  const fields = [];

  for (const el of scope.querySelectorAll('input, select, textarea')) {
    if (!isFillable(el)) continue;

    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'radio') {
      const key = el.name || el.id;
      if (!key || seenRadioGroups.has(key)) continue;
      seenRadioGroups.add(key);
    }

    const label = deriveLabel(el);
    if (!label) continue;

    fields.push({
      element: el,
      label,
      type: classify(el),
      options: optionsFor(el),
    });
  }

  return fields;
}

/** File inputs that plausibly want a resume, best candidate first. */
export function findResumeInputs(root) {
  const scope = root?.querySelectorAll ? root : root?.ownerDocument;
  if (!scope) return [];
  const inputs = Array.from(
    scope.querySelectorAll('input[type="file"]')
  ).filter(el => !el.disabled);
  const score = el => {
    const haystack =
      `${el.getAttribute('name') || ''} ${el.id || ''} ${deriveLabel(
        el
      )} ${el.getAttribute('accept') || ''}`.toLowerCase();
    if (/resume|cv\b|curriculum/.test(haystack)) return 2;
    if (/\.pdf|\.docx?|application\//.test(haystack)) return 1;
    return 0;
  };
  return inputs.sort((a, b) => score(b) - score(a));
}
