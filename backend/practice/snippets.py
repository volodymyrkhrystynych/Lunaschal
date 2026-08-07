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
    {
        'id': 'react-usequery',
        'language': 'react',
        'category': 'data',
        'title': 'useQuery',
        'code': (
            "const { data, isLoading } = useQuery({\n"
            "  queryKey: ['user', id],\n"
            "  queryFn: () => api.users.get(id),\n"
            '});'
        ),
    },
    {
        'id': 'react-usemutation',
        'language': 'react',
        'category': 'data',
        'title': 'useMutation',
        'code': (
            'const updateUser = useMutation({\n'
            '  mutationFn: api.users.update,\n'
            '  onSuccess: () => {\n'
            "    queryClient.invalidateQueries({ queryKey: ['user', id] });\n"
            '  },\n'
            '});'
        ),
    },
    {
        'id': 'react-usereducer',
        'language': 'react',
        'category': 'hooks',
        'title': 'useReducer',
        'code': (
            'function reducer(state, action) {\n'
            "  switch (action.type) {\n"
            "    case 'increment':\n"
            '      return { count: state.count + 1 };\n'
            '    default:\n'
            '      return state;\n'
            '  }\n'
            '}\n'
            'const [state, dispatch] = useReducer(reducer, { count: 0 });'
        ),
    },
    {
        'id': 'react-uselayouteffect',
        'language': 'react',
        'category': 'hooks',
        'title': 'useLayoutEffect',
        'code': (
            'useLayoutEffect(() => {\n'
            '  const { height } = ref.current.getBoundingClientRect();\n'
            '  setHeight(height);\n'
            '}, []);'
        ),
    },
    {
        'id': 'react-memo',
        'language': 'react',
        'category': 'performance',
        'title': 'React.memo',
        'code': (
            'const Row = memo(function Row({ item }) {\n'
            '  return <li>{item.name}</li>;\n'
            '});'
        ),
    },
    {
        'id': 'react-forwardref',
        'language': 'react',
        'category': 'refs',
        'title': 'forwardRef',
        'code': (
            'const Input = forwardRef(function Input(props, ref) {\n'
            '  return <input ref={ref} {...props} />;\n'
            '});'
        ),
    },
    {
        'id': 'react-controlled-input',
        'language': 'react',
        'category': 'forms',
        'title': 'Controlled input',
        'code': (
            'const [value, setValue] = useState(\'\');\n'
            '<input value={value} onChange={e => setValue(e.target.value)} />'
        ),
    },
    {
        'id': 'react-fragment',
        'language': 'react',
        'category': 'rendering',
        'title': 'Fragment shorthand',
        'code': '<>\n  <dt>{term}</dt>\n  <dd>{definition}</dd>\n</>',
    },
    {
        'id': 'react-children-prop',
        'language': 'react',
        'category': 'composition',
        'title': 'children prop',
        'code': (
            'function Panel({ title, children }) {\n'
            '  return (\n'
            '    <section>\n'
            '      <h2>{title}</h2>\n'
            '      {children}\n'
            '    </section>\n'
            '  );\n'
            '}'
        ),
    },
    {
        'id': 'react-error-boundary',
        'language': 'react',
        'category': 'error-handling',
        'title': 'Error boundary',
        'code': (
            'class ErrorBoundary extends React.Component {\n'
            '  state = { hasError: false };\n'
            '  static getDerivedStateFromError() {\n'
            '    return { hasError: true };\n'
            '  }\n'
            '  render() {\n'
            '    if (this.state.hasError) return <Fallback />;\n'
            '    return this.props.children;\n'
            '  }\n'
            '}'
        ),
    },
    {
        'id': 'react-portal',
        'language': 'react',
        'category': 'rendering',
        'title': 'createPortal',
        'code': (
            'createPortal(\n'
            '  <div className="modal">{children}</div>,\n'
            '  document.body\n'
            ')'
        ),
    },
    {
        'id': 'react-context-provider',
        'language': 'react',
        'category': 'hooks',
        'title': 'Context provider',
        'code': (
            'const ThemeContext = createContext(\'light\');\n\n'
            'function App() {\n'
            '  return (\n'
            '    <ThemeContext.Provider value="dark">\n'
            '      <Page />\n'
            '    </ThemeContext.Provider>\n'
            '  );\n'
            '}'
        ),
    },
    {
        'id': 'react-use-event-listener',
        'language': 'react',
        'category': 'hooks',
        'title': 'Custom hook with cleanup',
        'code': (
            'function useEventListener(event, handler) {\n'
            '  useEffect(() => {\n'
            '    window.addEventListener(event, handler);\n'
            '    return () => window.removeEventListener(event, handler);\n'
            '  }, [event, handler]);\n'
            '}'
        ),
    },
    {
        'id': 'react-lazy-suspense',
        'language': 'react',
        'category': 'performance',
        'title': 'Lazy loading + Suspense',
        'code': (
            "const Settings = lazy(() => import('./Settings'));\n\n"
            '<Suspense fallback={<Spinner />}>\n'
            '  <Settings />\n'
            '</Suspense>'
        ),
    },
    {
        'id': 'react-useid',
        'language': 'react',
        'category': 'hooks',
        'title': 'useId',
        'code': (
            'const id = useId();\n'
            '<label htmlFor={id}>Name</label>\n'
            '<input id={id} />'
        ),
    },
    {
        'id': 'react-usetransition',
        'language': 'react',
        'category': 'performance',
        'title': 'useTransition',
        'code': (
            'const [isPending, startTransition] = useTransition();\n\n'
            'function handleChange(value) {\n'
            '  startTransition(() => {\n'
            '    setResults(filterItems(value));\n'
            '  });\n'
            '}'
        ),
    },
    {
        'id': 'react-conditional-classname',
        'language': 'react',
        'category': 'conditionals',
        'title': 'Conditional className',
        'code': "<button className={`btn ${isActive ? 'btn-active' : ''}`}>Save</button>",
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
    {
        'id': 'js-optional-chaining',
        'language': 'javascript',
        'category': 'operators',
        'title': 'Optional chaining',
        'code': 'const city = user?.address?.city ?? \'Unknown\';',
    },
    {
        'id': 'js-nullish-coalescing',
        'language': 'javascript',
        'category': 'operators',
        'title': 'Nullish coalescing',
        'code': 'const pageSize = options.pageSize ?? 20;',
    },
    {
        'id': 'js-array-find',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.find',
        'code': 'const admin = users.find(u => u.role === \'admin\');',
    },
    {
        'id': 'js-array-some-every',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.some / Array.every',
        'code': (
            'const hasAdmin = users.some(u => u.role === \'admin\');\n'
            'const allActive = users.every(u => u.active);'
        ),
    },
    {
        'id': 'js-set-dedup',
        'language': 'javascript',
        'category': 'collections',
        'title': 'Dedupe with Set',
        'code': 'const unique = [...new Set(values)];',
    },
    {
        'id': 'js-map-usage',
        'language': 'javascript',
        'category': 'collections',
        'title': 'Map usage',
        'code': (
            "const counts = new Map();\n"
            'counts.set(\'apple\', 3);\n'
            "console.log(counts.get('apple'));"
        ),
    },
    {
        'id': 'js-promise-all',
        'language': 'javascript',
        'category': 'async',
        'title': 'Promise.all',
        'code': (
            'const [user, posts] = await Promise.all([\n'
            '  fetchUser(id),\n'
            '  fetchPosts(id),\n'
            ']);'
        ),
    },
    {
        'id': 'js-debounce',
        'language': 'javascript',
        'category': 'functions',
        'title': 'Debounce',
        'code': (
            'function debounce(fn, delay) {\n'
            '  let timer;\n'
            '  return (...args) => {\n'
            '    clearTimeout(timer);\n'
            '    timer = setTimeout(() => fn(...args), delay);\n'
            '  };\n'
            '}'
        ),
    },
    {
        'id': 'js-class',
        'language': 'javascript',
        'category': 'classes',
        'title': 'Class with constructor',
        'code': (
            'class Rectangle {\n'
            '  constructor(width, height) {\n'
            '    this.width = width;\n'
            '    this.height = height;\n'
            '  }\n'
            '  get area() {\n'
            '    return this.width * this.height;\n'
            '  }\n'
            '}'
        ),
    },
    {
        'id': 'js-default-params',
        'language': 'javascript',
        'category': 'functions',
        'title': 'Default parameters',
        'code': 'function greet(name = \'friend\') {\n  return `Hello, ${name}!`;\n}',
    },
    {
        'id': 'js-rest-params',
        'language': 'javascript',
        'category': 'functions',
        'title': 'Rest parameters',
        'code': 'function sum(...numbers) {\n  return numbers.reduce((a, b) => a + b, 0);\n}',
    },
    {
        'id': 'js-switch',
        'language': 'javascript',
        'category': 'conditionals',
        'title': 'Switch statement',
        'code': (
            "switch (status) {\n"
            "  case 'loading':\n"
            "    return 'Loading...';\n"
            "  case 'error':\n"
            "    return 'Something went wrong';\n"
            '  default:\n'
            "    return 'Ready';\n"
            '}'
        ),
    },
    {
        'id': 'js-try-catch-finally',
        'language': 'javascript',
        'category': 'error-handling',
        'title': 'try/catch/finally',
        'code': (
            'try {\n'
            '  saveDraft(entry);\n'
            '} catch (err) {\n'
            '  reportError(err);\n'
            '} finally {\n'
            '  setSaving(false);\n'
            '}'
        ),
    },
    {
        'id': 'js-json-parse-stringify',
        'language': 'javascript',
        'category': 'serialization',
        'title': 'JSON.stringify / parse',
        'code': (
            'localStorage.setItem(\'draft\', JSON.stringify(entry));\n'
            "const restored = JSON.parse(localStorage.getItem('draft'));"
        ),
    },
    {
        'id': 'js-array-sort',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.sort with comparator',
        'code': 'const sorted = [...items].sort((a, b) => a.price - b.price);',
    },
    {
        'id': 'js-flat-flatmap',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'flat / flatMap',
        'code': (
            'const tags = posts.flatMap(post => post.tags);\n'
            'const nested = [[1, 2], [3, [4, 5]]].flat(2);'
        ),
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
    {
        'id': 'html-meta-viewport',
        'language': 'html',
        'category': 'layout',
        'title': 'Meta viewport',
        'code': '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
    },
    {
        'id': 'html-video',
        'language': 'html',
        'category': 'media',
        'title': 'Video element',
        'code': '<video controls poster="thumb.jpg">\n  <source src="clip.mp4" type="video/mp4">\n</video>',
    },
    {
        'id': 'html-audio',
        'language': 'html',
        'category': 'media',
        'title': 'Audio element',
        'code': '<audio controls src="voice-memo.mp3"></audio>',
    },
    {
        'id': 'html-details-summary',
        'language': 'html',
        'category': 'elements',
        'title': 'Details / summary',
        'code': '<details>\n  <summary>More info</summary>\n  <p>Hidden until expanded.</p>\n</details>',
    },
    {
        'id': 'html-dialog',
        'language': 'html',
        'category': 'elements',
        'title': 'Dialog element',
        'code': '<dialog id="confirm">\n  <p>Are you sure?</p>\n  <button autofocus>OK</button>\n</dialog>',
    },
    {
        'id': 'html-progress',
        'language': 'html',
        'category': 'forms',
        'title': 'Progress bar',
        'code': '<progress value="70" max="100"></progress>',
    },
    {
        'id': 'html-datalist',
        'language': 'html',
        'category': 'forms',
        'title': 'Datalist suggestions',
        'code': (
            '<input list="browsers" name="browser">\n'
            '<datalist id="browsers">\n'
            '  <option value="Chrome">\n'
            '  <option value="Firefox">\n'
            '</datalist>'
        ),
    },
    {
        'id': 'html-textarea',
        'language': 'html',
        'category': 'forms',
        'title': 'Textarea',
        'code': '<label for="notes">Notes</label>\n<textarea id="notes" rows="4"></textarea>',
    },
    {
        'id': 'html-fieldset',
        'language': 'html',
        'category': 'forms',
        'title': 'Fieldset / legend',
        'code': (
            '<fieldset>\n'
            '  <legend>Shipping address</legend>\n'
            '  <input name="street">\n'
            '</fieldset>'
        ),
    },
    {
        'id': 'html-radio-group',
        'language': 'html',
        'category': 'forms',
        'title': 'Radio group',
        'code': (
            '<input type="radio" id="light" name="theme" value="light">\n'
            '<label for="light">Light</label>\n'
            '<input type="radio" id="dark" name="theme" value="dark">\n'
            '<label for="dark">Dark</label>'
        ),
    },
    {
        'id': 'html-checkbox',
        'language': 'html',
        'category': 'forms',
        'title': 'Checkbox',
        'code': '<input type="checkbox" id="agree" name="agree">\n<label for="agree">I agree</label>',
    },
    {
        'id': 'html-figure',
        'language': 'html',
        'category': 'elements',
        'title': 'Figure / figcaption',
        'code': '<figure>\n  <img src="chart.png" alt="Sales chart">\n  <figcaption>Q3 sales</figcaption>\n</figure>',
    },
    {
        'id': 'html-nav',
        'language': 'html',
        'category': 'layout',
        'title': 'Nav with links',
        'code': '<nav>\n  <a href="/">Home</a>\n  <a href="/about">About</a>\n</nav>',
    },
    {
        'id': 'html-article-section',
        'language': 'html',
        'category': 'layout',
        'title': 'Article / section',
        'code': '<article>\n  <section>\n    <h2>Overview</h2>\n  </section>\n</article>',
    },
    {
        'id': 'html-iframe',
        'language': 'html',
        'category': 'elements',
        'title': 'Iframe',
        'code': '<iframe src="https://example.com/embed" title="Embedded content" loading="lazy"></iframe>',
    },
    {
        'id': 'html-data-attribute',
        'language': 'html',
        'category': 'elements',
        'title': 'Custom data attribute',
        'code': '<li data-id="42" data-status="done">Buy milk</li>',
    },
    {
        'id': 'html-aria-label',
        'language': 'html',
        'category': 'accessibility',
        'title': 'ARIA label',
        'code': '<button aria-label="Close dialog">&times;</button>',
    },
    {
        'id': 'html-picture-srcset',
        'language': 'html',
        'category': 'media',
        'title': 'Picture with srcset',
        'code': (
            '<picture>\n'
            '  <source srcset="photo-large.jpg" media="(min-width: 800px)">\n'
            '  <img src="photo-small.jpg" alt="A landscape">\n'
            '</picture>'
        ),
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
    {
        'id': 'css-grid-areas',
        'language': 'css',
        'category': 'layout',
        'title': 'Grid template areas',
        'code': (
            '.layout {\n'
            '  display: grid;\n'
            '  grid-template-areas:\n'
            '    "sidebar header"\n'
            '    "sidebar main";\n'
            '}\n'
            '.sidebar { grid-area: sidebar; }'
        ),
    },
    {
        'id': 'css-flex-wrap',
        'language': 'css',
        'category': 'layout',
        'title': 'Flex wrap',
        'code': '.tags {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 8px;\n}',
    },
    {
        'id': 'css-calc',
        'language': 'css',
        'category': 'sizing',
        'title': 'calc()',
        'code': '.sidebar {\n  width: calc(100% - 240px);\n}',
    },
    {
        'id': 'css-clamp',
        'language': 'css',
        'category': 'sizing',
        'title': 'clamp()',
        'code': 'h1 {\n  font-size: clamp(1.5rem, 4vw, 3rem);\n}',
    },
    {
        'id': 'css-keyframes',
        'language': 'css',
        'category': 'animation',
        'title': 'Keyframe animation',
        'code': (
            '@keyframes spin {\n'
            '  to { transform: rotate(360deg); }\n'
            '}\n'
            '.loader {\n'
            '  animation: spin 1s linear infinite;\n'
            '}'
        ),
    },
    {
        'id': 'css-transform',
        'language': 'css',
        'category': 'animation',
        'title': 'Transform',
        'code': '.card:hover {\n  transform: scale(1.05) rotate(-1deg);\n}',
    },
    {
        'id': 'css-before-pseudo',
        'language': 'css',
        'category': 'selectors',
        'title': '::before pseudo-element',
        'code': '.required::before {\n  content: "*";\n  color: red;\n  margin-right: 4px;\n}',
    },
    {
        'id': 'css-nth-child',
        'language': 'css',
        'category': 'selectors',
        'title': 'nth-child',
        'code': 'tr:nth-child(even) {\n  background: var(--color-bg-alt);\n}',
    },
    {
        'id': 'css-object-fit',
        'language': 'css',
        'category': 'media',
        'title': 'object-fit',
        'code': '.avatar {\n  width: 48px;\n  height: 48px;\n  object-fit: cover;\n}',
    },
    {
        'id': 'css-aspect-ratio',
        'language': 'css',
        'category': 'sizing',
        'title': 'aspect-ratio',
        'code': '.thumbnail {\n  aspect-ratio: 16 / 9;\n  width: 100%;\n}',
    },
    {
        'id': 'css-variable-fallback',
        'language': 'css',
        'category': 'variables',
        'title': 'Custom property with fallback',
        'code': '.button {\n  color: var(--accent-color, #3b82f6);\n}',
    },
    {
        'id': 'css-container-query',
        'language': 'css',
        'category': 'responsive',
        'title': 'Container query',
        'code': (
            '.card-wrapper {\n'
            '  container-type: inline-size;\n'
            '}\n'
            '@container (min-width: 400px) {\n'
            '  .card {\n'
            '    flex-direction: row;\n'
            '  }\n'
            '}'
        ),
    },
    {
        'id': 'css-backdrop-filter',
        'language': 'css',
        'category': 'effects',
        'title': 'Backdrop filter',
        'code': '.overlay {\n  backdrop-filter: blur(8px);\n  background: rgba(0, 0, 0, 0.3);\n}',
    },
    {
        'id': 'css-sticky',
        'language': 'css',
        'category': 'layout',
        'title': 'Sticky positioning',
        'code': '.toolbar {\n  position: sticky;\n  top: 0;\n  z-index: 10;\n}',
    },
    {
        'id': 'css-text-ellipsis',
        'language': 'css',
        'category': 'typography',
        'title': 'Text overflow ellipsis',
        'code': (
            '.truncate {\n'
            '  white-space: nowrap;\n'
            '  overflow: hidden;\n'
            '  text-overflow: ellipsis;\n'
            '}'
        ),
    },
    {
        'id': 'css-attribute-selector',
        'language': 'css',
        'category': 'selectors',
        'title': 'Attribute selector',
        'code': 'input[type="email"]:invalid {\n  border-color: red;\n}',
    },
    # --- DOM / Browser APIs ---
    {
        'id': 'dom-query-selector',
        'language': 'dom',
        'category': 'selection',
        'title': 'querySelector / querySelectorAll',
        'code': (
            'const button = document.querySelector(\'.btn-primary\');\n'
            "const items = document.querySelectorAll('li.active');"
        ),
    },
    {
        'id': 'dom-add-event-listener',
        'language': 'dom',
        'category': 'events',
        'title': 'addEventListener',
        'code': (
            "button.addEventListener('click', event => {\n"
            '  console.log(\'clicked\', event.target);\n'
            '});'
        ),
    },
    {
        'id': 'dom-classlist',
        'language': 'dom',
        'category': 'selection',
        'title': 'classList add/remove/toggle',
        'code': (
            "el.classList.add('active');\n"
            "el.classList.remove('hidden');\n"
            "el.classList.toggle('open');"
        ),
    },
    {
        'id': 'dom-create-element',
        'language': 'dom',
        'category': 'manipulation',
        'title': 'createElement + appendChild',
        'code': (
            "const li = document.createElement('li');\n"
            "li.textContent = 'New item';\n"
            'list.appendChild(li);'
        ),
    },
    {
        'id': 'dom-dataset',
        'language': 'dom',
        'category': 'selection',
        'title': 'dataset access',
        'code': "const userId = event.target.dataset.userId;",
    },
    {
        'id': 'dom-fetch-post',
        'language': 'dom',
        'category': 'network',
        'title': 'fetch with headers + POST',
        'code': (
            "const res = await fetch('/api/users', {\n"
            "  method: 'POST',\n"
            "  headers: { 'Content-Type': 'application/json' },\n"
            '  body: JSON.stringify({ name }),\n'
            '});'
        ),
    },
    {
        'id': 'dom-local-storage',
        'language': 'dom',
        'category': 'storage',
        'title': 'localStorage get/set',
        'code': (
            "localStorage.setItem('theme', 'dark');\n"
            "const theme = localStorage.getItem('theme') ?? 'light';"
        ),
    },
    {
        'id': 'dom-session-storage',
        'language': 'dom',
        'category': 'storage',
        'title': 'sessionStorage',
        'code': (
            "sessionStorage.setItem('draftId', draftId);\n"
            "sessionStorage.removeItem('draftId');"
        ),
    },
    {
        'id': 'dom-set-timeout',
        'language': 'dom',
        'category': 'timers',
        'title': 'setTimeout / clearTimeout',
        'code': (
            'const timer = setTimeout(() => {\n'
            "  console.log('done');\n"
            '}, 2000);\n'
            'clearTimeout(timer);'
        ),
    },
    {
        'id': 'dom-set-interval',
        'language': 'dom',
        'category': 'timers',
        'title': 'setInterval / clearInterval',
        'code': (
            'const interval = setInterval(() => {\n'
            '  tick();\n'
            '}, 1000);\n'
            'clearInterval(interval);'
        ),
    },
    {
        'id': 'dom-form-data',
        'language': 'dom',
        'category': 'forms',
        'title': 'FormData',
        'code': (
            'const formData = new FormData(formEl);\n'
            "const email = formData.get('email');"
        ),
    },
    {
        'id': 'dom-prevent-default',
        'language': 'dom',
        'category': 'events',
        'title': 'preventDefault / stopPropagation',
        'code': (
            "form.addEventListener('submit', event => {\n"
            '  event.preventDefault();\n'
            '  event.stopPropagation();\n'
            '  submitForm();\n'
            '});'
        ),
    },
    {
        'id': 'dom-intersection-observer',
        'language': 'dom',
        'category': 'observers',
        'title': 'IntersectionObserver',
        'code': (
            'const observer = new IntersectionObserver(entries => {\n'
            '  entries.forEach(entry => {\n'
            '    if (entry.isIntersecting) loadMore();\n'
            '  });\n'
            '});\n'
            'observer.observe(sentinel);'
        ),
    },
    {
        'id': 'dom-mutation-observer',
        'language': 'dom',
        'category': 'observers',
        'title': 'MutationObserver',
        'code': (
            'const observer = new MutationObserver(mutations => {\n'
            '  console.log(mutations.length, \'changes\');\n'
            '});\n'
            'observer.observe(target, { childList: true, subtree: true });'
        ),
    },
    {
        'id': 'dom-match-media',
        'language': 'dom',
        'category': 'responsive',
        'title': 'matchMedia',
        'code': (
            "const isDark = window.matchMedia('(prefers-color-scheme: dark)');\n"
            "isDark.addEventListener('change', e => applyTheme(e.matches));"
        ),
    },
    {
        'id': 'dom-clipboard',
        'language': 'dom',
        'category': 'misc',
        'title': 'Clipboard API',
        'code': "await navigator.clipboard.writeText(shareUrl);",
    },
    {
        'id': 'dom-history-pushstate',
        'language': 'dom',
        'category': 'navigation',
        'title': 'history.pushState',
        'code': "history.pushState({ page: 2 }, '', '/items?page=2');",
    },
    {
        'id': 'dom-request-animation-frame',
        'language': 'dom',
        'category': 'timers',
        'title': 'requestAnimationFrame',
        'code': (
            'function tick() {\n'
            '  updatePosition();\n'
            '  requestAnimationFrame(tick);\n'
            '}\n'
            'requestAnimationFrame(tick);'
        ),
    },
    {
        'id': 'dom-custom-event',
        'language': 'dom',
        'category': 'events',
        'title': 'CustomEvent + dispatchEvent',
        'code': (
            "const event = new CustomEvent('item-added', { detail: { id } });\n"
            'document.dispatchEvent(event);'
        ),
    },
    {
        'id': 'dom-closest',
        'language': 'dom',
        'category': 'selection',
        'title': 'closest()',
        'code': "const card = event.target.closest('.card');",
    },
]

SNIPPETS_BY_ID = {s['id']: s for s in SNIPPETS}
LANGUAGES = ('react', 'javascript', 'html', 'css', 'dom')
