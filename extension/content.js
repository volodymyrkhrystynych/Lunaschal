/**
 * The part that touches the page: read the form, fill it, attach the resume.
 *
 * Injected on demand by the service worker, as a classic script, so it holds
 * no imports at the top level — `lib/fields.js` arrives through a dynamic
 * import of an extension URL (which is why it is in web_accessible_resources).
 *
 * It never calls the backend. Everything goes through the service worker; see
 * the CORS note in background.js.
 */
(() => {
  // Re-injection is normal — every context-menu click calls executeScript when
  // the ping fails, and a race can let two through.
  if (window.__lunaschalApply) return;
  window.__lunaschalApply = true;

  let fieldsModule = null;
  async function fields() {
    if (!fieldsModule) {
      fieldsModule = await import(chrome.runtime.getURL('lib/fields.js'));
    }
    return fieldsModule;
  }

  function send(type, payload = {}) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type, ...payload }, response => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (!response?.ok) {
          reject(new Error(response?.error || 'Lunaschal failed.'));
        } else {
          resolve(response);
        }
      });
    });
  }

  // ------------------------------------------------------------------------
  // Writing into a field
  // ------------------------------------------------------------------------

  /**
   * Set a control's value so that React notices.
   *
   * React tracks the last value it wrote on the DOM node and ignores an
   * `input` event whose value matches, so a plain `el.value = x` updates the
   * pixels and leaves the component state stale — the form then submits empty.
   * Going through the prototype's native setter is what defeats the tracker.
   * Nearly every modern ATS is React, so this is the normal path, not a
   * workaround for one site. Do not "simplify" it back to `el.value =`.
   */
  function setNativeValue(el, value) {
    const prototype =
      {
        textarea: HTMLTextAreaElement,
        select: HTMLSelectElement,
      }[el.tagName.toLowerCase()] || HTMLInputElement;

    const setter = Object.getOwnPropertyDescriptor(
      prototype.prototype,
      'value'
    )?.set;
    if (setter) setter.call(el, value);
    else el.value = value;

    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function setChecked(el, checked) {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'checked'
    )?.set;
    if (setter) setter.call(el, checked);
    else el.checked = checked;
    el.dispatchEvent(new Event('click', { bubbles: true }));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  const AFFIRMATIVE = /^\s*(yes|true|y|1)\b/i;

  /** Pick the option whose text or value best matches `answer`. */
  function selectOption(el, answer) {
    const wanted = String(answer).trim().toLowerCase();
    const options = Array.from(el.options || []);
    const match =
      options.find(o => o.text.trim().toLowerCase() === wanted) ||
      options.find(o => o.value.trim().toLowerCase() === wanted) ||
      options.find(o => o.text.trim().toLowerCase().includes(wanted)) ||
      options.find(
        o => wanted.includes(o.text.trim().toLowerCase()) && o.text.trim()
      );
    if (!match) return false;
    setNativeValue(el, match.value);
    return true;
  }

  function fillOne(field, answer) {
    if (!answer) return false;
    const el = field.element;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();

    if (tag === 'select') return selectOption(el, answer);

    if (type === 'checkbox') {
      setChecked(el, AFFIRMATIVE.test(answer));
      return true;
    }

    if (type === 'radio') {
      const group = Array.from(
        (el.form || document).querySelectorAll(
          `input[type="radio"][name="${CSS.escape(el.name)}"]`
        )
      );
      const wanted = String(answer).trim().toLowerCase();
      const chosen =
        group.find(r => (r.value || '').trim().toLowerCase() === wanted) ||
        group.find(r => labelTextFor(r).toLowerCase() === wanted) ||
        group.find(r => labelTextFor(r).toLowerCase().includes(wanted));
      if (!chosen) return false;
      setChecked(chosen, true);
      return true;
    }

    setNativeValue(el, answer);
    return true;
  }

  function labelTextFor(el) {
    return (el.closest('label')?.textContent || el.value || '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  // ------------------------------------------------------------------------
  // Attaching the resume
  // ------------------------------------------------------------------------

  /**
   * Put a File into a file input.
   *
   * `input.files` is only assignable from a `FileList`, and `DataTransfer` is
   * the one way to build one. Unlike text fields, React reads file inputs
   * straight off the DOM, so a plain `change` event is enough here.
   */
  function attachFile(input, { name, base64, mimeType }) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);

    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], name, { type: mimeType }));
    input.files = transfer.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ------------------------------------------------------------------------
  // The overlay
  // ------------------------------------------------------------------------
  //
  // In a shadow root so the page's CSS cannot restyle it into invisibility,
  // which on a heavily-styled careers page is otherwise a matter of time.

  let host = null;
  let shadow = null;

  function panel() {
    if (shadow) return shadow;
    host = document.createElement('div');
    host.id = 'lunaschal-apply-root';
    shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = `
      <style>
        .panel {
          position: fixed; right: 16px; bottom: 16px; width: 340px;
          max-height: 60vh; overflow-y: auto; z-index: 2147483647;
          font: 13px/1.45 system-ui, sans-serif; color: #f4f4f5;
          background: #18181b; border: 1px solid #3f3f46; border-radius: 10px;
          box-shadow: 0 10px 30px rgba(0,0,0,.45); padding: 12px;
        }
        .row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .title { font-weight: 600; flex: 1; }
        .close { cursor: pointer; border: 0; background: none; color: #a1a1aa;
                 font-size: 16px; line-height: 1; }
        .item { border-top: 1px solid #27272a; padding: 8px 0; }
        .q { color: #a1a1aa; font-size: 12px; }
        .a { white-space: pre-wrap; }
        .badge { font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
                 padding: 1px 5px; border-radius: 4px; border: 1px solid #3f3f46;
                 color: #a1a1aa; margin-left: 6px; }
        .badge.profile, .badge.bank { color: #4ade80; border-color: #14532d; }
        .badge.unanswered { color: #fbbf24; border-color: #78350f; }
        .error { color: #fca5a5; }
        .actions { display: flex; gap: 6px; margin-top: 10px; }
        button.act { flex: 1; cursor: pointer; padding: 6px 8px; border-radius: 6px;
                     border: 1px solid #3f3f46; background: #27272a; color: #f4f4f5; }
        button.act:hover { background: #3f3f46; }
      </style>
      <div class="panel"><div class="row"><span class="title">Lunaschal</span>
      <button class="close" title="Close">×</button></div>
      <div class="body"></div><div class="actions"></div></div>
    `;
    shadow.querySelector('.close').addEventListener('click', () => {
      host.remove();
      host = null;
      shadow = null;
    });
    document.documentElement.appendChild(host);
    return shadow;
  }

  function render({ title, items = [], error = '', actions = [] }) {
    const root = panel();
    root.querySelector('.title').textContent = title || 'Lunaschal';

    const body = root.querySelector('.body');
    body.textContent = '';
    if (error) {
      const div = document.createElement('div');
      div.className = 'error';
      div.textContent = error;
      body.appendChild(div);
    }
    for (const item of items) {
      const wrap = document.createElement('div');
      wrap.className = 'item';
      const q = document.createElement('div');
      q.className = 'q';
      q.textContent = item.label;
      const badge = document.createElement('span');
      badge.className = `badge ${item.source}`;
      badge.textContent = item.source;
      q.appendChild(badge);
      const a = document.createElement('div');
      a.className = 'a';
      a.textContent = item.answer || '—';
      wrap.append(q, a);
      body.appendChild(wrap);
    }

    const bar = root.querySelector('.actions');
    bar.textContent = '';
    for (const action of actions) {
      const button = document.createElement('button');
      button.className = 'act';
      button.textContent = action.label;
      button.addEventListener('click', action.onClick);
      bar.appendChild(button);
    }
  }

  // ------------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------------

  const pageUrl = () => location.href;

  async function recordFilled(filled) {
    if (!filled.length) return;
    await send('recordAnswers', {
      answers: filled.map(f => ({
        question: f.label,
        answer: f.answer,
        source: f.source,
        pageUrl: pageUrl(),
      })),
    });
  }

  async function fillForm() {
    render({ title: 'Reading the form…' });
    const { collectFields } = await fields();
    const found = collectFields(document);
    if (!found.length) {
      render({
        title: 'Lunaschal',
        error: 'No fillable questions found on this page.',
      });
      return;
    }

    render({
      title: `Answering ${found.length} question${found.length === 1 ? '' : 's'}…`,
    });
    // `answers.py` caps one batch at MAX_QUESTIONS (40) and returns a *short
    // list* rather than erroring, so a long Workday form would quietly leave
    // its tail empty. Chunking keeps the "one model call per batch" win — a
    // 100-field form costs three calls, not a hundred.
    const CHUNK = 40;
    const answers = [];
    try {
      for (let start = 0; start < found.length; start += CHUNK) {
        const batch = found.slice(start, start + CHUNK);
        const response = await send('answerQuestions', {
          questions: batch.map(f => ({
            label: f.label,
            type: f.type,
            options: f.options,
          })),
        });
        answers.push(...response.answers);
        if (found.length > CHUNK) {
          render({ title: `Answering… ${answers.length} of ${found.length}` });
        }
      }
    } catch (error) {
      render({ title: 'Lunaschal', error: error.message });
      return;
    }

    const filled = [];
    answers.forEach((answer, index) => {
      const field = found[index];
      if (!field || answer.source === 'unanswered') return;
      if (fillOne(field, answer.answer)) {
        filled.push({
          label: field.label,
          answer: answer.answer,
          source: answer.source,
        });
      }
    });

    render({
      title: `Filled ${filled.length} of ${found.length}`,
      items: answers.map((a, i) => ({
        label: found[i]?.label ?? a.label,
        answer: a.answer,
        source: a.source,
      })),
      actions: [
        {
          label: 'Save answers to Lunaschal',
          onClick: async () => {
            try {
              const { written } = await send('recordAnswers', {
                answers: answers.map((a, i) => ({
                  question: found[i]?.label ?? a.label,
                  // The user may have corrected a field after it was filled;
                  // read the DOM back rather than trusting what was sent.
                  answer: currentValue(found[i]) ?? a.answer,
                  source: a.source,
                  pageUrl: pageUrl(),
                })),
              });
              render({ title: `Saved ${written} answers` });
            } catch (error) {
              render({ title: 'Lunaschal', error: error.message });
            }
          },
        },
        { label: 'Attach resume', onClick: attachResume },
      ],
    });

    // Record immediately as well as on the button: intercepting a submit is
    // unreliable on SPA forms, many of which never fire one.
    recordFilled(filled).catch(() => {});
  }

  function currentValue(field) {
    if (!field?.element) return null;
    const el = field.element;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'checkbox') return el.checked ? 'Yes' : 'No';
    if (type === 'radio') {
      const group = Array.from(
        (el.form || document).querySelectorAll(
          `input[type="radio"][name="${CSS.escape(el.name)}"]`
        )
      );
      const chosen = group.find(r => r.checked);
      return chosen ? labelTextFor(chosen) : null;
    }
    return el.value ?? null;
  }

  let lastEditable = null;
  document.addEventListener(
    'contextmenu',
    event => {
      const target = event.target;
      if (target?.matches?.('input, textarea, select, [contenteditable]')) {
        lastEditable = target;
      }
    },
    true
  );

  async function answerFocused() {
    const el =
      lastEditable && document.contains(lastEditable)
        ? lastEditable
        : document.activeElement;
    if (!el || !el.matches?.('input, textarea, select')) {
      render({
        title: 'Lunaschal',
        error: 'Right-click inside a form field first.',
      });
      return;
    }

    const { deriveLabel, classify, optionsFor } = await fields();
    const label = deriveLabel(el);
    if (!label) {
      render({
        title: 'Lunaschal',
        error: 'That field has no readable label.',
      });
      return;
    }

    render({ title: `Answering “${label}”…` });
    try {
      const { answers } = await send('answerQuestions', {
        questions: [{ label, type: classify(el), options: optionsFor(el) }],
      });
      const answer = answers[0];
      if (!answer || answer.source === 'unanswered') {
        render({
          title: 'Lunaschal',
          error: 'No answer could be produced for that.',
        });
        return;
      }
      fillOne({ element: el, label }, answer.answer);
      render({
        title: 'Filled',
        items: [{ label, answer: answer.answer, source: answer.source }],
      });
      recordFilled([
        { label, answer: answer.answer, source: answer.source },
      ]).catch(() => {});
    } catch (error) {
      render({ title: 'Lunaschal', error: error.message });
    }
  }

  async function attachResume() {
    render({ title: 'Fetching your resume…' });
    try {
      const { findResumeInputs } = await fields();
      const inputs = findResumeInputs(document);
      if (!inputs.length) {
        render({
          title: 'Lunaschal',
          error: 'No file upload found on this page.',
        });
        return;
      }
      const file = await send('resume', { ext: 'pdf' });
      attachFile(inputs[0], file);
      render({ title: `Attached ${file.name}` });
    } catch (error) {
      render({ title: 'Lunaschal', error: error.message });
    }
  }

  /** Insert dictated text at the caret of the last field the user touched. */
  function insertDictation(text) {
    const el =
      lastEditable && document.contains(lastEditable) ? lastEditable : null;
    if (!el) return false;
    const existing = el.value ?? '';
    setNativeValue(el, existing ? `${existing} ${text}` : text);
    return true;
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    const actions = {
      ping: () => ({ ok: true }),
      fillForm: () => {
        fillForm();
        return { ok: true };
      },
      answerFocused: () => {
        answerFocused();
        return { ok: true };
      },
      attachResume: () => {
        attachResume();
        return { ok: true };
      },
      insertDictation: () => ({ ok: insertDictation(message.text || '') }),
    };
    const action = actions[message?.type];
    if (!action) return false;
    sendResponse(action());
    return false;
  });
})();
