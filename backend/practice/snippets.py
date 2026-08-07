"""The curated bank of drill snippets for the Practice tab.

Content lives here, not in the DB — a fixed, git-versioned list rather than a
user-editable library, so extending it is a PR. `id` is a stable slug (not a
ULID) since it identifies content, not a generated row; per-snippet progress
(attempts/accuracy/wpm) lives in the `practice_progress` table keyed by it.
"""

SNIPPETS = [
    # --- React ---
    {
        'id': 'react-usestate',
        'language': 'react',
        'category': 'hooks',
        'title': 'useState',
        'code': 'const [count, setCount] = useState(0);',
    },
    {
        'id': 'react-useeffect',
        'language': 'react',
        'category': 'hooks',
        'title': 'useEffect',
        'code': 'useEffect(() => {\n  fetchData();\n}, [id]);',
    },
    {
        'id': 'react-useref',
        'language': 'react',
        'category': 'hooks',
        'title': 'useRef',
        'code': 'const inputRef = useRef(null);\ninputRef.current?.focus();',
    },
    {
        'id': 'react-usememo',
        'language': 'react',
        'category': 'hooks',
        'title': 'useMemo',
        'code': 'const total = useMemo(\n  () => items.reduce((sum, i) => sum + i.price, 0),\n  [items]\n);',
    },
    {
        'id': 'react-usecallback',
        'language': 'react',
        'category': 'hooks',
        'title': 'useCallback',
        'code': 'const handleClick = useCallback(() => {\n  onSelect(id);\n}, [id, onSelect]);',
    },
    {
        'id': 'react-usecontext',
        'language': 'react',
        'category': 'hooks',
        'title': 'useContext',
        'code': 'const theme = useContext(ThemeContext);',
    },
    {
        'id': 'react-custom-hook',
        'language': 'react',
        'category': 'hooks',
        'title': 'Custom hook',
        'code': (
            'function useToggle(initial = false) {\n'
            '  const [value, setValue] = useState(initial);\n'
            '  const toggle = () => setValue(v => !v);\n'
            '  return [value, toggle];\n'
            '}'
        ),
    },
    {
        'id': 'react-map-key',
        'language': 'react',
        'category': 'rendering',
        'title': 'List rendering with key',
        'code': '{items.map(item => (\n  <li key={item.id}>{item.name}</li>\n))}',
    },
    {
        'id': 'react-conditional',
        'language': 'react',
        'category': 'conditionals',
        'title': 'Conditional render',
        'code': '{isLoading ? <Spinner /> : <Content data={data} />}',
    },
    {
        'id': 'react-props-destructure',
        'language': 'react',
        'category': 'destructuring',
        'title': 'Props destructuring',
        'code': 'function Card({ title, subtitle, onClick }) {\n  return <div onClick={onClick}>{title}</div>;\n}',
    },
    # --- JavaScript ---
    {
        'id': 'js-for-loop',
        'language': 'javascript',
        'category': 'loops',
        'title': 'for loop',
        'code': 'for (let i = 0; i < items.length; i++) {\n  console.log(items[i]);\n}',
    },
    {
        'id': 'js-for-of',
        'language': 'javascript',
        'category': 'loops',
        'title': 'for...of',
        'code': 'for (const item of items) {\n  console.log(item);\n}',
    },
    {
        'id': 'js-map-filter-reduce',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'map/filter/reduce chain',
        'code': (
            'const total = items\n'
            '  .filter(i => i.active)\n'
            '  .map(i => i.price)\n'
            '  .reduce((sum, price) => sum + price, 0);'
        ),
    },
    {
        'id': 'js-arrow-function',
        'language': 'javascript',
        'category': 'functions',
        'title': 'Arrow function',
        'code': 'const add = (a, b) => a + b;',
    },
    {
        'id': 'js-array-destructure',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Array destructuring',
        'code': 'const [first, second, ...rest] = values;',
    },
    {
        'id': 'js-object-destructure',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Object destructuring',
        'code': 'const { id, name, ...rest } = user;',
    },
    {
        'id': 'js-template-literal',
        'language': 'javascript',
        'category': 'strings',
        'title': 'Template literal',
        'code': 'const message = `Hello, ${name}! You have ${count} new messages.`;',
    },
    {
        'id': 'js-async-await',
        'language': 'javascript',
        'category': 'async',
        'title': 'async/await + try/catch',
        'code': (
            'async function loadUser(id) {\n'
            '  try {\n'
            '    const res = await fetch(`/api/users/${id}`);\n'
            '    return await res.json();\n'
            '  } catch (err) {\n'
            '    console.error(err);\n'
            '  }\n'
            '}'
        ),
    },
    {
        'id': 'js-fetch',
        'language': 'javascript',
        'category': 'async',
        'title': 'fetch with .then',
        'code': "fetch('/api/data')\n  .then(res => res.json())\n  .then(data => console.log(data));",
    },
    {
        'id': 'js-spread',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Object spread',
        'code': 'const merged = { ...defaults, ...overrides };',
    },
    # --- HTML ---
    {
        'id': 'html-boilerplate',
        'language': 'html',
        'category': 'layout',
        'title': 'Page boilerplate',
        'code': (
            '<!DOCTYPE html>\n'
            '<html lang="en">\n'
            '<head>\n'
            '  <meta charset="UTF-8">\n'
            '  <title>Document</title>\n'
            '</head>\n'
            '<body>\n'
            '</body>\n'
            '</html>'
        ),
    },
    {
        'id': 'html-form-input',
        'language': 'html',
        'category': 'forms',
        'title': 'Labeled input',
        'code': '<label for="email">Email</label>\n<input type="email" id="email" name="email" required>',
    },
    {
        'id': 'html-semantic-layout',
        'language': 'html',
        'category': 'layout',
        'title': 'Semantic layout skeleton',
        'code': '<header></header>\n<main></main>\n<footer></footer>',
    },
    {
        'id': 'html-anchor-img',
        'language': 'html',
        'category': 'elements',
        'title': 'Linked image',
        'code': '<a href="https://example.com" target="_blank" rel="noopener">\n  <img src="photo.jpg" alt="A photo">\n</a>',
    },
    {
        'id': 'html-table',
        'language': 'html',
        'category': 'elements',
        'title': 'Table skeleton',
        'code': (
            '<table>\n'
            '  <thead>\n'
            '    <tr><th>Name</th><th>Age</th></tr>\n'
            '  </thead>\n'
            '  <tbody>\n'
            '    <tr><td>Alice</td><td>30</td></tr>\n'
            '  </tbody>\n'
            '</table>'
        ),
    },
    {
        'id': 'html-list',
        'language': 'html',
        'category': 'elements',
        'title': 'Unordered list',
        'code': '<ul>\n  <li>First</li>\n  <li>Second</li>\n</ul>',
    },
    {
        'id': 'html-button',
        'language': 'html',
        'category': 'forms',
        'title': 'Submit button',
        'code': '<button type="submit" class="btn-primary">Submit</button>',
    },
    {
        'id': 'html-select',
        'language': 'html',
        'category': 'forms',
        'title': 'Select dropdown',
        'code': '<select name="color">\n  <option value="red">Red</option>\n  <option value="blue">Blue</option>\n</select>',
    },
    # --- CSS ---
    {
        'id': 'css-flex-center',
        'language': 'css',
        'category': 'layout',
        'title': 'Flexbox center',
        'code': '.container {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n}',
    },
    {
        'id': 'css-grid-template',
        'language': 'css',
        'category': 'layout',
        'title': 'Grid template',
        'code': '.grid {\n  display: grid;\n  grid-template-columns: repeat(3, 1fr);\n  gap: 16px;\n}',
    },
    {
        'id': 'css-media-query',
        'language': 'css',
        'category': 'responsive',
        'title': 'Media query',
        'code': '@media (max-width: 768px) {\n  .sidebar {\n    display: none;\n  }\n}',
    },
    {
        'id': 'css-transition',
        'language': 'css',
        'category': 'animation',
        'title': 'Transition',
        'code': '.button {\n  transition: background-color 0.2s ease-in-out;\n}',
    },
    {
        'id': 'css-custom-property',
        'language': 'css',
        'category': 'variables',
        'title': 'Custom property',
        'code': ':root {\n  --primary-color: #3b82f6;\n}\n.button {\n  color: var(--primary-color);\n}',
    },
    {
        'id': 'css-hover-focus',
        'language': 'css',
        'category': 'selectors',
        'title': 'Hover/focus selector',
        'code': '.link:hover,\n.link:focus {\n  text-decoration: underline;\n}',
    },
    {
        'id': 'css-box-shadow',
        'language': 'css',
        'category': 'effects',
        'title': 'Box shadow',
        'code': '.card {\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);\n}',
    },
    {
        'id': 'css-selectors',
        'language': 'css',
        'category': 'selectors',
        'title': 'Child + pseudo-class selector',
        'code': '.list > li:first-child {\n  font-weight: bold;\n}',
    },
    {
        'id': 'css-position-absolute',
        'language': 'css',
        'category': 'layout',
        'title': 'Absolute positioning',
        'code': '.badge {\n  position: absolute;\n  top: 0;\n  right: 0;\n}',
    },
]

SNIPPETS_BY_ID = {s['id']: s for s in SNIPPETS}
LANGUAGES = ('react', 'javascript', 'html', 'css')
