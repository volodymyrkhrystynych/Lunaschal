"""The curated bank of drill snippets for the Practice tab.

Content lives here, not in the DB — a fixed, git-versioned list rather than a
user-editable library, so extending it is a PR. `id` is a stable slug (not a
ULID) since it identifies content, not a generated row; per-snippet progress
(attempts/accuracy/wpm) lives in the `practice_progress` table keyed by it.

Every snippet carries a `prompt` as well as its `code`: the task as the blind
drill states it, when the code is withheld and the snippet has to be written
from memory. It is the only thing the writer sees, so it has to pin down
everything that would otherwise be guesswork — the identifiers and literal
values the reference uses, and any detail that changes what the code does — and
nothing beyond that. `backend/ai/practice.py` grades the answer against this
sentence, not against the reference character for character, so a prompt that
underspecifies its snippet reads as an unfair grade rather than a vague
question. Adding a snippet without a prompt breaks the blind drill for it;
`test_every_snippet_has_a_prompt` is the guard.

What a snippet *means* lives next door in `explanations.py`, keyed by the same
id, so this file stays a bank of code. A snippet whose code needs an import to
run carries that import as its first line — the drill is worth nothing if it
teaches the hook call and not where the hook comes from — and its prompt says so,
since the blind grade is made against the prompt.
"""

SNIPPETS = [
    # --- React ---
    {
        'id': 'react-usestate',
        'language': 'react',
        'category': 'hooks',
        'title': 'useState',
        'prompt': (
            'Import the hook from React, then declare a state variable `count` starting at 0, '
            'together with its setter.'
        ),
        'code': "import { useState } from 'react';\n\nconst [count, setCount] = useState(0);",
    },
    {
        'id': 'react-useeffect',
        'language': 'react',
        'category': 'hooks',
        'title': 'useEffect',
        'prompt': (
            'Import the hook from React, then call `fetchData()` from an effect that re-runs '
            'whenever `id` changes.'
        ),
        'code': (
            "import { useEffect } from 'react';\n\n"
            'useEffect(() => {\n  fetchData();\n}, [id]);'
        ),
    },
    {
        'id': 'react-useref',
        'language': 'react',
        'category': 'hooks',
        'title': 'useRef',
        'prompt': (
            'Import the hook from React, then create a ref for an input and focus it, guarding '
            'against the ref being empty.'
        ),
        'code': (
            "import { useRef } from 'react';\n\n"
            'const inputRef = useRef(null);\ninputRef.current?.focus();'
        ),
    },
    {
        'id': 'react-usememo',
        'language': 'react',
        'category': 'hooks',
        'title': 'useMemo',
        'prompt': (
            'Import the hook from React, then memoize a `total` that sums the `price` of every '
            'entry in `items`, recomputed only when `items` changes.'
        ),
        'code': (
            "import { useMemo } from 'react';\n\n"
            'const total = useMemo(\n  () => items.reduce((sum, i) => sum + i.price, 0),\n  [items]\n);'
        ),
    },
    {
        'id': 'react-usecallback',
        'language': 'react',
        'category': 'hooks',
        'title': 'useCallback',
        'prompt': (
            'Import the hook from React, then memoize a `handleClick` that calls `onSelect(id)`, '
            'keeping the same function while `id` and `onSelect` are unchanged.'
        ),
        'code': (
            "import { useCallback } from 'react';\n\n"
            'const handleClick = useCallback(() => {\n  onSelect(id);\n}, [id, onSelect]);'
        ),
    },
    {
        'id': 'react-usecontext',
        'language': 'react',
        'category': 'hooks',
        'title': 'useContext',
        'prompt': (
            'Import the hook from React, then read the current value of `ThemeContext` into a '
            '`theme` variable.'
        ),
        'code': (
            "import { useContext } from 'react';\n\n"
            'const theme = useContext(ThemeContext);'
        ),
    },
    {
        'id': 'react-custom-hook',
        'language': 'react',
        'category': 'hooks',
        'title': 'Custom hook',
        'prompt': (
            'Importing whatever it needs from React, write a `useToggle(initial = false)` hook '
            'returning the boolean and a function that flips it, updating from the previous value.'
        ),
        'code': (
            "import { useState } from 'react';\n\n"
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
        'prompt': (
            'Render `items` as a list of `<li>` elements in JSX, each showing `item.name` and '
            'identified by its id.'
        ),
        'code': '{items.map(item => (\n  <li key={item.id}>{item.name}</li>\n))}',
    },
    {
        'id': 'react-conditional',
        'language': 'react',
        'category': 'conditionals',
        'title': 'Conditional render',
        'prompt': (
            'In JSX, render `<Spinner />` while `isLoading`, otherwise `<Content />` with `data` '
            'passed as a prop.'
        ),
        'code': (
            '{isLoading ? (\n'
            '  <Spinner />\n'
            ') : (\n'
            '  <Content data={data} />\n'
            ')}'
        ),
    },
    {
        'id': 'react-props-destructure',
        'language': 'react',
        'category': 'destructuring',
        'title': 'Props destructuring',
        'prompt': (
            'Write a `Card` component that destructures `title`, `subtitle` and `onClick` in its '
            'parameter list and returns a div showing the title and firing onClick.'
        ),
        'code': 'function Card({ title, subtitle, onClick }) {\n  return <div onClick={onClick}>{title}</div>;\n}',
    },
    {
        'id': 'react-usequery',
        'language': 'react',
        'category': 'data',
        'title': 'useQuery',
        'prompt': (
            "Fetch a user with React Query, importing the hook from its package: key it on "
            "`['user', id]`, call `api.users.get(id)`, and take `data` and `isLoading` off the "
            "result."
        ),
        'code': (
            "import { useQuery } from '@tanstack/react-query';\n\n"
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
        'prompt': (
            "Importing what you need from React Query, take the query client and set up a "
            "mutation calling `api.users.update` that invalidates the `['user', id]` query when it "
            "succeeds."
        ),
        'code': (
            'import {\n'
            '  useMutation,\n'
            '  useQueryClient,\n'
            "} from '@tanstack/react-query';\n"
            '\n'
            'const queryClient = useQueryClient();\n'
            'const updateUser = useMutation({\n'
            '  mutationFn: api.users.update,\n'
            '  onSuccess: () => {\n'
            '    queryClient.invalidateQueries({\n'
            "      queryKey: ['user', id],\n"
            '    });\n'
            '  },\n'
            '});'
        ),
    },
    {
        'id': 'react-usereducer',
        'language': 'react',
        'category': 'hooks',
        'title': 'useReducer',
        'prompt': (
            'Importing the hook from React, write a reducer handling an `increment` action by '
            'adding 1 to `state.count` and returning the state untouched by default, then wire it '
            'up with useReducer starting from a count of 0.'
        ),
        'code': (
            "import { useReducer } from 'react';\n"
            '\n'
            'function reducer(state, action) {\n'
            '  switch (action.type) {\n'
            "    case 'increment':\n"
            '      return { count: state.count + 1 };\n'
            '    default:\n'
            '      return state;\n'
            '  }\n'
            '}\n'
            'const [state, dispatch] = useReducer(\n'
            '  reducer,\n'
            '  { count: 0 }\n'
            ');'
        ),
    },
    {
        'id': 'react-uselayouteffect',
        'language': 'react',
        'category': 'hooks',
        'title': 'useLayoutEffect',
        'prompt': (
            'Importing the hook from React, measure the height of `ref.current` with '
            'getBoundingClientRect and store it with `setHeight`, before the browser paints, once '
            'on mount.'
        ),
        'code': (
            "import { useLayoutEffect } from 'react';\n"
            '\n'
            'useLayoutEffect(() => {\n'
            '  const { height } =\n'
            '    ref.current.getBoundingClientRect();\n'
            '  setHeight(height);\n'
            '}, []);'
        ),
    },
    {
        'id': 'react-memo',
        'language': 'react',
        'category': 'performance',
        'title': 'React.memo',
        'prompt': (
            'Importing it from React, wrap a `Row` component rendering `item.name` in an `<li>` so '
            'it only re-renders when its props change.'
        ),
        'code': (
            "import { memo } from 'react';\n\n"
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
        'prompt': (
            'Importing it from React, write an `Input` component that forwards a ref onto the '
            'underlying `<input>` and spreads the rest of its props onto it.'
        ),
        'code': (
            "import { forwardRef } from 'react';\n"
            '\n'
            'const Input = forwardRef(\n'
            '  function Input(props, ref) {\n'
            '    return <input ref={ref} {...props} />;\n'
            '  }\n'
            ');'
        ),
    },
    {
        'id': 'react-controlled-input',
        'language': 'react',
        'category': 'forms',
        'title': 'Controlled input',
        'prompt': (
            "Importing the hook from React, hold an input's text in state and make it a controlled "
            "input: value from state, onChange writing the event's value back."
        ),
        'code': (
            "import { useState } from 'react';\n"
            '\n'
            "const [value, setValue] = useState('');\n"
            '<input\n'
            '  value={value}\n'
            '  onChange={e => setValue(e.target.value)}\n'
            '/>'
        ),
    },
    {
        'id': 'react-fragment',
        'language': 'react',
        'category': 'rendering',
        'title': 'Fragment shorthand',
        'prompt': (
            'Return a `<dt>` and `<dd>` pair from JSX with no wrapper element, using the shorthand '
            'syntax.'
        ),
        'code': '<>\n  <dt>{term}</dt>\n  <dd>{definition}</dd>\n</>',
    },
    {
        'id': 'react-children-prop',
        'language': 'react',
        'category': 'composition',
        'title': 'children prop',
        'prompt': (
            'Write a `Panel` component taking `title` and `children`, rendering a section with the '
            'title as an `<h2>` above the children.'
        ),
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
        'prompt': (
            'Write an error boundary class component: a `hasError` state, the static lifecycle '
            'method that flips it when a child throws, and a render showing `<Fallback />` when it '
            'is set and the children otherwise. Import React itself for the base class.'
        ),
        'code': (
            "import React from 'react';\n\n"
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
        'prompt': (
            'Render a modal div into `document.body` through a portal, importing it from the '
            'package it actually lives in.'
        ),
        'code': (
            "import { createPortal } from 'react-dom';\n\n"
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
        'prompt': (
            "Importing it from React, create a `ThemeContext` defaulting to 'light', then have an "
            "`App` render `<Page />` inside a provider supplying 'dark'."
        ),
        'code': (
            "import { createContext } from 'react';\n\n"
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
        'prompt': (
            'Importing the hook from React, write a `useEventListener(event, handler)` hook adding '
            'a window listener in an effect and removing it in the cleanup, re-running when either '
            'argument changes.'
        ),
        'code': (
            "import { useEffect } from 'react';\n"
            '\n'
            'function useEventListener(event, handler) {\n'
            '  useEffect(() => {\n'
            '    window.addEventListener(event, handler);\n'
            '    return () =>\n'
            '      window.removeEventListener(event, handler);\n'
            '  }, [event, handler]);\n'
            '}'
        ),
    },
    {
        'id': 'react-lazy-suspense',
        'language': 'react',
        'category': 'performance',
        'title': 'Lazy loading + Suspense',
        'prompt': (
            'Importing both from React, lazily load a `Settings` component and render it inside a '
            'Suspense boundary falling back to `<Spinner />`.'
        ),
        'code': (
            "import { lazy, Suspense } from 'react';\n\n"
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
        'prompt': (
            'Importing it from React, generate a unique id with the hook for it and use it to tie '
            'a `<label>` to its `<input>`.'
        ),
        'code': (
            "import { useId } from 'react';\n\n"
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
        'prompt': (
            'Importing the hook from React, mark a `setResults(filterItems(value))` update as '
            'non-urgent with a transition, also taking the pending flag.'
        ),
        'code': (
            "import { useTransition } from 'react';\n"
            '\n'
            'const [isPending, startTransition] =\n'
            '  useTransition();\n'
            '\n'
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
        'prompt': (
            'Give a button the class `btn`, plus `btn-active` only while `isActive`, built with a '
            'template literal.'
        ),
        'code': (
            '<button\n'
            "  className={`btn ${isActive ? 'btn-active' : ''}`}\n"
            '>\n'
            '  Save\n'
            '</button>'
        ),
    },
    # --- JavaScript ---
    {
        'id': 'js-for-loop',
        'language': 'javascript',
        'category': 'loops',
        'title': 'for loop',
        'prompt': 'Log every entry of `items` with a classic indexed for loop.',
        'code': 'for (let i = 0; i < items.length; i++) {\n  console.log(items[i]);\n}',
    },
    {
        'id': 'js-for-of',
        'language': 'javascript',
        'category': 'loops',
        'title': 'for...of',
        'prompt': 'Log every entry of `items` with a for...of loop.',
        'code': 'for (const item of items) {\n  console.log(item);\n}',
    },
    {
        'id': 'js-map-filter-reduce',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'map/filter/reduce chain',
        'prompt': (
            "From `items`, keep the active ones, take each one's `price`, and sum them into `total` "
            "in a single chain."
        ),
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
        'prompt': (
            'Define `add` as an arrow function of two numbers returning their sum, with no function '
            'body braces.'
        ),
        'code': 'const add = (a, b) => a + b;',
    },
    {
        'id': 'js-array-destructure',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Array destructuring',
        'prompt': (
            'Pull the first two entries of `values` into `first` and `second`, collecting the '
            'remainder into `rest`.'
        ),
        'code': 'const [first, second, ...rest] = values;',
    },
    {
        'id': 'js-object-destructure',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Object destructuring',
        'prompt': 'Pull `id` and `name` off `user`, collecting the remaining properties into `rest`.',
        'code': 'const { id, name, ...rest } = user;',
    },
    {
        'id': 'js-template-literal',
        'language': 'javascript',
        'category': 'strings',
        'title': 'Template literal',
        'prompt': 'Build a greeting string interpolating `name` and `count` with a template literal.',
        'code': (
            'const message =\n'
            '  `Hello, ${name}! You have ${count} messages.`;'
        ),
    },
    {
        'id': 'js-async-await',
        'language': 'javascript',
        'category': 'async',
        'title': 'async/await + try/catch',
        'prompt': (
            'Write an async `loadUser(id)` that awaits a fetch of the users endpoint for that id, '
            'returns the parsed JSON, and logs anything thrown from a catch.'
        ),
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
        'prompt': 'Fetch `/api/data` and log the parsed JSON, chaining with .then rather than awaiting.',
        'code': "fetch('/api/data')\n  .then(res => res.json())\n  .then(data => console.log(data));",
    },
    {
        'id': 'js-spread',
        'language': 'javascript',
        'category': 'destructuring',
        'title': 'Object spread',
        'prompt': 'Build `merged` from `defaults` overridden by `overrides`, using object spread.',
        'code': 'const merged = { ...defaults, ...overrides };',
    },
    {
        'id': 'js-optional-chaining',
        'language': 'javascript',
        'category': 'operators',
        'title': 'Optional chaining',
        'prompt': "Read `user.address.city` safely when either link may be missing, defaulting to 'Unknown'.",
        'code': 'const city = user?.address?.city ?? \'Unknown\';',
    },
    {
        'id': 'js-nullish-coalescing',
        'language': 'javascript',
        'category': 'operators',
        'title': 'Nullish coalescing',
        'prompt': 'Read `options.pageSize`, defaulting to 20 only when it is null or undefined.',
        'code': 'const pageSize = options.pageSize ?? 20;',
    },
    {
        'id': 'js-array-find',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.find',
        'prompt': "Find the first entry of `users` whose role is 'admin'.",
        'code': 'const admin = users.find(u => u.role === \'admin\');',
    },
    {
        'id': 'js-array-some-every',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.some / Array.every',
        'prompt': (
            'Check whether any entry of `users` is an admin, and separately whether every one of '
            'them is active.'
        ),
        'code': (
            'const hasAdmin = users.some(\n'
            "  u => u.role === 'admin'\n"
            ');\n'
            'const allActive = users.every(u => u.active);'
        ),
    },
    {
        'id': 'js-set-dedup',
        'language': 'javascript',
        'category': 'collections',
        'title': 'Dedupe with Set',
        'prompt': 'Turn `values` into a new array with the duplicates removed, by way of a Set.',
        'code': 'const unique = [...new Set(values)];',
    },
    {
        'id': 'js-map-usage',
        'language': 'javascript',
        'category': 'collections',
        'title': 'Map usage',
        'prompt': "Create a Map, store 3 under the key 'apple', and log the value read back out of it.",
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
        'prompt': (
            'Await `fetchUser(id)` and `fetchPosts(id)` concurrently rather than one after the '
            'other, destructuring both results.'
        ),
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
        'prompt': (
            'Write a `debounce(fn, delay)` returning a wrapper that resets its timer on every call '
            'and forwards all its arguments when it finally fires.'
        ),
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
        'prompt': (
            'Write a `Rectangle` class taking width and height in its constructor, with an `area` '
            'getter returning their product.'
        ),
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
        'prompt': (
            "Write a `greet(name)` defaulting the name to 'friend' and returning a greeting built "
            "with a template literal."
        ),
        'code': 'function greet(name = \'friend\') {\n  return `Hello, ${name}!`;\n}',
    },
    {
        'id': 'js-rest-params',
        'language': 'javascript',
        'category': 'functions',
        'title': 'Rest parameters',
        'prompt': (
            'Write a `sum` taking any number of arguments as a rest parameter and reducing them to '
            'a total.'
        ),
        'code': 'function sum(...numbers) {\n  return numbers.reduce((a, b) => a + b, 0);\n}',
    },
    {
        'id': 'js-switch',
        'language': 'javascript',
        'category': 'conditionals',
        'title': 'Switch statement',
        'prompt': (
            "Switch on `status`, returning a loading message for 'loading', an error message for "
            "'error', and a ready message by default."
        ),
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
        'prompt': (
            'Call `saveDraft(entry)` guarded by try/catch, reporting the error, and clear the '
            'saving flag whether or not it threw.'
        ),
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
        'prompt': (
            "Serialize `entry` into localStorage under the key 'draft', then read it back out and "
            "parse it."
        ),
        'code': (
            'localStorage.setItem(\n'
            "  'draft',\n"
            '  JSON.stringify(entry)\n'
            ');\n'
            'const restored = JSON.parse(\n'
            "  localStorage.getItem('draft')\n"
            ');'
        ),
    },
    {
        'id': 'js-array-sort',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'Array.sort with comparator',
        'prompt': 'Sort `items` by ascending `price` without mutating the original array.',
        'code': (
            'const sorted = [...items].sort(\n'
            '  (a, b) => a.price - b.price\n'
            ');'
        ),
    },
    {
        'id': 'js-flat-flatmap',
        'language': 'javascript',
        'category': 'iteration',
        'title': 'flat / flatMap',
        'prompt': (
            'Collect every tag across `posts` with flatMap, and separately flatten a two-level '
            'nested array.'
        ),
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
        'prompt': (
            'Write a minimal HTML5 document: the doctype, an html element with a language, a head '
            'carrying the UTF-8 charset and a title, and an empty body.'
        ),
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
        'prompt': 'Write a required email input with a matching name and id, tied to its own label.',
        'code': (
            '<label for="email">Email</label>\n'
            '<input\n'
            '  type="email"\n'
            '  id="email"\n'
            '  name="email"\n'
            '  required\n'
            '>'
        ),
    },
    {
        'id': 'html-semantic-layout',
        'language': 'html',
        'category': 'layout',
        'title': 'Semantic layout skeleton',
        'prompt': (
            'Write the three top-level semantic landmarks of a page — header, main and footer — all '
            'empty.'
        ),
        'code': '<header></header>\n<main></main>\n<footer></footer>',
    },
    {
        'id': 'html-anchor-img',
        'language': 'html',
        'category': 'elements',
        'title': 'Linked image',
        'prompt': 'Wrap an image in a link that opens in a new tab safely, giving the image alt text.',
        'code': (
            '<a\n'
            '  href="https://example.com"\n'
            '  target="_blank"\n'
            '  rel="noopener"\n'
            '>\n'
            '  <img src="photo.jpg" alt="A photo">\n'
            '</a>'
        ),
    },
    {
        'id': 'html-table',
        'language': 'html',
        'category': 'elements',
        'title': 'Table skeleton',
        'prompt': (
            'Write a table with a Name/Age header row and one row of data, each in its proper '
            'section element.'
        ),
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
        'prompt': 'Write an unordered list of two items.',
        'code': '<ul>\n  <li>First</li>\n  <li>Second</li>\n</ul>',
    },
    {
        'id': 'html-button',
        'language': 'html',
        'category': 'forms',
        'title': 'Submit button',
        'prompt': 'Write a submit button carrying the class `btn-primary`.',
        'code': (
            '<button type="submit" class="btn-primary">\n'
            '  Submit\n'
            '</button>'
        ),
    },
    {
        'id': 'html-select',
        'language': 'html',
        'category': 'forms',
        'title': 'Select dropdown',
        'prompt': 'Write a dropdown named `color` offering red and blue, each option carrying a value.',
        'code': '<select name="color">\n  <option value="red">Red</option>\n  <option value="blue">Blue</option>\n</select>',
    },
    {
        'id': 'html-meta-viewport',
        'language': 'html',
        'category': 'layout',
        'title': 'Meta viewport',
        'prompt': 'Write the viewport meta tag that maps the layout to the device width at a scale of 1.',
        'code': (
            '<meta\n'
            '  name="viewport"\n'
            '  content="width=device-width, initial-scale=1.0"\n'
            '>'
        ),
    },
    {
        'id': 'html-video',
        'language': 'html',
        'category': 'media',
        'title': 'Video element',
        'prompt': (
            'Embed an mp4 with visible controls and a poster image, pointing at the file through a '
            'nested source element.'
        ),
        'code': '<video controls poster="thumb.jpg">\n  <source src="clip.mp4" type="video/mp4">\n</video>',
    },
    {
        'id': 'html-audio',
        'language': 'html',
        'category': 'media',
        'title': 'Audio element',
        'prompt': 'Embed an mp3 with controls, sourced from an attribute rather than a child element.',
        'code': '<audio controls src="voice-memo.mp3"></audio>',
    },
    {
        'id': 'html-details-summary',
        'language': 'html',
        'category': 'elements',
        'title': 'Details / summary',
        'prompt': "Write a collapsible disclosure labelled 'More info' with a paragraph hidden inside it.",
        'code': '<details>\n  <summary>More info</summary>\n  <p>Hidden until expanded.</p>\n</details>',
    },
    {
        'id': 'html-dialog',
        'language': 'html',
        'category': 'elements',
        'title': 'Dialog element',
        'prompt': (
            'Write a dialog with an id, a confirmation question and an OK button that takes focus '
            'when it opens.'
        ),
        'code': '<dialog id="confirm">\n  <p>Are you sure?</p>\n  <button autofocus>OK</button>\n</dialog>',
    },
    {
        'id': 'html-progress',
        'language': 'html',
        'category': 'forms',
        'title': 'Progress bar',
        'prompt': 'Write a progress bar sitting at 70 out of 100.',
        'code': '<progress value="70" max="100"></progress>',
    },
    {
        'id': 'html-datalist',
        'language': 'html',
        'category': 'forms',
        'title': 'Datalist suggestions',
        'prompt': (
            'Offer an input a list of suggestions (Chrome, Firefox) through a datalist, wired to it '
            'by id.'
        ),
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
        'prompt': 'Write a four-row textarea with an id, tied to its own label.',
        'code': '<label for="notes">Notes</label>\n<textarea id="notes" rows="4"></textarea>',
    },
    {
        'id': 'html-fieldset',
        'language': 'html',
        'category': 'forms',
        'title': 'Fieldset / legend',
        'prompt': "Group a street input inside a fieldset captioned 'Shipping address'.",
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
        'prompt': 'Write two radio buttons in one `theme` group, light and dark, each with its own label.',
        'code': (
            '<input\n'
            '  type="radio"\n'
            '  id="light"\n'
            '  name="theme"\n'
            '  value="light"\n'
            '>\n'
            '<label for="light">Light</label>\n'
            '<input\n'
            '  type="radio"\n'
            '  id="dark"\n'
            '  name="theme"\n'
            '  value="dark"\n'
            '>\n'
            '<label for="dark">Dark</label>'
        ),
    },
    {
        'id': 'html-checkbox',
        'language': 'html',
        'category': 'forms',
        'title': 'Checkbox',
        'prompt': 'Write a checkbox with a matching name and id, tied to its own label.',
        'code': '<input type="checkbox" id="agree" name="agree">\n<label for="agree">I agree</label>',
    },
    {
        'id': 'html-figure',
        'language': 'html',
        'category': 'elements',
        'title': 'Figure / figcaption',
        'prompt': 'Write a figure holding an image with alt text and a caption underneath it.',
        'code': '<figure>\n  <img src="chart.png" alt="Sales chart">\n  <figcaption>Q3 sales</figcaption>\n</figure>',
    },
    {
        'id': 'html-nav',
        'language': 'html',
        'category': 'layout',
        'title': 'Nav with links',
        'prompt': 'Write a nav containing links to the home and about pages.',
        'code': '<nav>\n  <a href="/">Home</a>\n  <a href="/about">About</a>\n</nav>',
    },
    {
        'id': 'html-article-section',
        'language': 'html',
        'category': 'layout',
        'title': 'Article / section',
        'prompt': 'Nest a section carrying an `<h2>` inside an article.',
        'code': '<article>\n  <section>\n    <h2>Overview</h2>\n  </section>\n</article>',
    },
    {
        'id': 'html-iframe',
        'language': 'html',
        'category': 'elements',
        'title': 'Iframe',
        'prompt': 'Embed an external page in an iframe with an accessible title, loaded lazily.',
        'code': (
            '<iframe\n'
            '  src="https://example.com/embed"\n'
            '  title="Embedded content"\n'
            '  loading="lazy"\n'
            '></iframe>'
        ),
    },
    {
        'id': 'html-data-attribute',
        'language': 'html',
        'category': 'elements',
        'title': 'Custom data attribute',
        'prompt': 'Write a list item carrying custom data attributes for an id and a status.',
        'code': '<li data-id="42" data-status="done">Buy milk</li>',
    },
    {
        'id': 'html-aria-label',
        'language': 'html',
        'category': 'accessibility',
        'title': 'ARIA label',
        'prompt': 'Write a close button whose only content is a times entity, given an accessible name.',
        'code': '<button aria-label="Close dialog">&times;</button>',
    },
    {
        'id': 'html-picture-srcset',
        'language': 'html',
        'category': 'media',
        'title': 'Picture with srcset',
        'prompt': (
            'Serve a large image above 800px wide and a small one otherwise, through a picture '
            'element with a fallback img carrying alt text.'
        ),
        'code': (
            '<picture>\n'
            '  <source\n'
            '    srcset="photo-large.jpg"\n'
            '    media="(min-width: 800px)"\n'
            '  >\n'
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
        'prompt': "Centre a container's children both horizontally and vertically with flexbox.",
        'code': '.container {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n}',
    },
    {
        'id': 'css-grid-template',
        'language': 'css',
        'category': 'layout',
        'title': 'Grid template',
        'prompt': 'Lay a grid out in three equal columns with a 16px gap.',
        'code': '.grid {\n  display: grid;\n  grid-template-columns: repeat(3, 1fr);\n  gap: 16px;\n}',
    },
    {
        'id': 'css-media-query',
        'language': 'css',
        'category': 'responsive',
        'title': 'Media query',
        'prompt': 'Hide `.sidebar` at viewports 768px wide and under.',
        'code': '@media (max-width: 768px) {\n  .sidebar {\n    display: none;\n  }\n}',
    },
    {
        'id': 'css-transition',
        'language': 'css',
        'category': 'animation',
        'title': 'Transition',
        'prompt': "Transition a button's background colour over 0.2s on an ease-in-out curve.",
        'code': '.button {\n  transition: background-color 0.2s ease-in-out;\n}',
    },
    {
        'id': 'css-custom-property',
        'language': 'css',
        'category': 'variables',
        'title': 'Custom property',
        'prompt': (
            "Define a `--primary-color` custom property on the root, then use it as a button's text "
            "colour."
        ),
        'code': ':root {\n  --primary-color: #3b82f6;\n}\n.button {\n  color: var(--primary-color);\n}',
    },
    {
        'id': 'css-hover-focus',
        'language': 'css',
        'category': 'selectors',
        'title': 'Hover/focus selector',
        'prompt': 'Underline a link on both hover and keyboard focus, in a single rule.',
        'code': '.link:hover,\n.link:focus {\n  text-decoration: underline;\n}',
    },
    {
        'id': 'css-box-shadow',
        'language': 'css',
        'category': 'effects',
        'title': 'Box shadow',
        'prompt': (
            'Give a card a soft drop shadow: no horizontal offset, 2px down, 8px of blur, black at '
            '15%.'
        ),
        'code': '.card {\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);\n}',
    },
    {
        'id': 'css-selectors',
        'language': 'css',
        'category': 'selectors',
        'title': 'Child + pseudo-class selector',
        'prompt': 'Bold only the first `<li>` that is a direct child of `.list`.',
        'code': '.list > li:first-child {\n  font-weight: bold;\n}',
    },
    {
        'id': 'css-position-absolute',
        'language': 'css',
        'category': 'layout',
        'title': 'Absolute positioning',
        'prompt': 'Pin a badge to the top-right corner of its positioned ancestor.',
        'code': '.badge {\n  position: absolute;\n  top: 0;\n  right: 0;\n}',
    },
    {
        'id': 'css-grid-areas',
        'language': 'css',
        'category': 'layout',
        'title': 'Grid template areas',
        'prompt': (
            'Lay a grid out by named areas — a sidebar spanning two rows beside a header and a main '
            '— and assign the sidebar to its area.'
        ),
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
        'prompt': 'Lay tags out in a row that wraps onto further lines, with an 8px gap.',
        'code': '.tags {\n  display: flex;\n  flex-wrap: wrap;\n  gap: 8px;\n}',
    },
    {
        'id': 'css-calc',
        'language': 'css',
        'category': 'sizing',
        'title': 'calc()',
        'prompt': 'Size a sidebar to the full width minus 240px.',
        'code': '.sidebar {\n  width: calc(100% - 240px);\n}',
    },
    {
        'id': 'css-clamp',
        'language': 'css',
        'category': 'sizing',
        'title': 'clamp()',
        'prompt': 'Scale an h1 with the viewport between 1.5rem and 3rem, preferring 4vw.',
        'code': 'h1 {\n  font-size: clamp(1.5rem, 4vw, 3rem);\n}',
    },
    {
        'id': 'css-keyframes',
        'language': 'css',
        'category': 'animation',
        'title': 'Keyframe animation',
        'prompt': (
            'Define a `spin` keyframe rotating a full turn, and apply it to a loader looping once a '
            'second at a constant speed.'
        ),
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
        'prompt': 'On hover, scale a card up 5% and tilt it one degree anticlockwise, in one transform.',
        'code': '.card:hover {\n  transform: scale(1.05) rotate(-1deg);\n}',
    },
    {
        'id': 'css-before-pseudo',
        'language': 'css',
        'category': 'selectors',
        'title': '::before pseudo-element',
        'prompt': 'Insert a red asterisk before `.required`, with 4px of space after it.',
        'code': '.required::before {\n  content: "*";\n  color: red;\n  margin-right: 4px;\n}',
    },
    {
        'id': 'css-nth-child',
        'language': 'css',
        'category': 'selectors',
        'title': 'nth-child',
        'prompt': 'Give every even table row a different background, taken from a custom property.',
        'code': 'tr:nth-child(even) {\n  background: var(--color-bg-alt);\n}',
    },
    {
        'id': 'css-object-fit',
        'language': 'css',
        'category': 'media',
        'title': 'object-fit',
        'prompt': 'Crop an avatar to a 48px square without distorting it.',
        'code': '.avatar {\n  width: 48px;\n  height: 48px;\n  object-fit: cover;\n}',
    },
    {
        'id': 'css-aspect-ratio',
        'language': 'css',
        'category': 'sizing',
        'title': 'aspect-ratio',
        'prompt': 'Hold a full-width thumbnail at a 16:9 shape.',
        'code': '.thumbnail {\n  aspect-ratio: 16 / 9;\n  width: 100%;\n}',
    },
    {
        'id': 'css-variable-fallback',
        'language': 'css',
        'category': 'variables',
        'title': 'Custom property with fallback',
        'prompt': 'Colour a button from `--accent-color`, falling back to a blue hex when it is not set.',
        'code': '.button {\n  color: var(--accent-color, #3b82f6);\n}',
    },
    {
        'id': 'css-container-query',
        'language': 'css',
        'category': 'responsive',
        'title': 'Container query',
        'prompt': (
            'Make a wrapper a size container, then switch the card inside it to a row once the '
            'container is at least 400px wide.'
        ),
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
        'prompt': 'Blur whatever sits behind an overlay by 8px, over a 30% black tint.',
        'code': '.overlay {\n  backdrop-filter: blur(8px);\n  background: rgba(0, 0, 0, 0.3);\n}',
    },
    {
        'id': 'css-sticky',
        'language': 'css',
        'category': 'layout',
        'title': 'Sticky positioning',
        'prompt': 'Stick a toolbar to the top of its scroll container, above the content it scrolls over.',
        'code': '.toolbar {\n  position: sticky;\n  top: 0;\n  z-index: 10;\n}',
    },
    {
        'id': 'css-text-ellipsis',
        'language': 'css',
        'category': 'typography',
        'title': 'Text overflow ellipsis',
        'prompt': 'Truncate one line of text with an ellipsis — all three properties it takes.',
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
        'prompt': (
            "Turn an email input's border red while its value is invalid, selecting it by its type "
            "attribute."
        ),
        'code': 'input[type="email"]:invalid {\n  border-color: red;\n}',
    },
    # --- DOM / Browser APIs ---
    {
        'id': 'dom-query-selector',
        'language': 'dom',
        'category': 'selection',
        'title': 'querySelector / querySelectorAll',
        'prompt': 'Grab the first `.btn-primary` element, and separately every `li.active`.',
        'code': (
            'const button =\n'
            "  document.querySelector('.btn-primary');\n"
            'const items =\n'
            "  document.querySelectorAll('li.active');"
        ),
    },
    {
        'id': 'dom-add-event-listener',
        'language': 'dom',
        'category': 'events',
        'title': 'addEventListener',
        'prompt': "Listen for clicks on `button` and log the event's target.",
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
        'prompt': 'On `el`, add one class, remove another, and toggle a third.',
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
        'prompt': 'Build an `<li>` with text in it and append it to `list`.',
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
        'prompt': "Read a `data-user-id` attribute off an event's target through the dataset API.",
        'code': "const userId = event.target.dataset.userId;",
    },
    {
        'id': 'dom-fetch-post',
        'language': 'dom',
        'category': 'network',
        'title': 'fetch with headers + POST',
        'prompt': (
            'POST a JSON body of `{ name }` to `/api/users` with the matching content-type header, '
            'awaiting the response.'
        ),
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
        'prompt': "Store a theme of 'dark' in localStorage, then read it back defaulting to 'light'.",
        'code': (
            "localStorage.setItem('theme', 'dark');\n"
            'const theme =\n'
            "  localStorage.getItem('theme') ?? 'light';"
        ),
    },
    {
        'id': 'dom-session-storage',
        'language': 'dom',
        'category': 'storage',
        'title': 'sessionStorage',
        'prompt': 'Store `draftId` in sessionStorage, then remove it again.',
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
        'prompt': 'Schedule a log two seconds out, keeping the handle, then cancel it.',
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
        'prompt': 'Call `tick()` once a second, keeping the handle, then cancel it.',
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
        'prompt': 'Read the email field out of a form element through FormData.',
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
        'prompt': (
            "On a form's submit, stop the page reloading and stop the event bubbling, then call "
            "`submitForm()`."
        ),
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
        'prompt': 'Observe a sentinel element and call `loadMore()` whenever it comes into view.',
        'code': (
            'const observer = new IntersectionObserver(\n'
            '  entries => {\n'
            '    entries.forEach(entry => {\n'
            '      if (entry.isIntersecting) loadMore();\n'
            '    });\n'
            '  }\n'
            ');\n'
            'observer.observe(sentinel);'
        ),
    },
    {
        'id': 'dom-mutation-observer',
        'language': 'dom',
        'category': 'observers',
        'title': 'MutationObserver',
        'prompt': (
            'Watch a target for nodes added or removed anywhere beneath it, logging how many '
            'mutations fired.'
        ),
        'code': (
            'const observer = new MutationObserver(mutations => {\n'
            "  console.log(mutations.length, 'changes');\n"
            '});\n'
            'observer.observe(target, {\n'
            '  childList: true,\n'
            '  subtree: true,\n'
            '});'
        ),
    },
    {
        'id': 'dom-match-media',
        'language': 'dom',
        'category': 'responsive',
        'title': 'matchMedia',
        'prompt': 'Query the dark-scheme preference and re-apply the theme whenever it changes.',
        'code': (
            'const isDark = window.matchMedia(\n'
            "  '(prefers-color-scheme: dark)'\n"
            ');\n'
            "isDark.addEventListener('change', e =>\n"
            '  applyTheme(e.matches)\n'
            ');'
        ),
    },
    {
        'id': 'dom-clipboard',
        'language': 'dom',
        'category': 'misc',
        'title': 'Clipboard API',
        'prompt': 'Copy `shareUrl` to the clipboard, awaiting it.',
        'code': "await navigator.clipboard.writeText(shareUrl);",
    },
    {
        'id': 'dom-history-pushstate',
        'language': 'dom',
        'category': 'navigation',
        'title': 'history.pushState',
        'prompt': 'Push a new history entry for page 2 at `/items?page=2`, carrying that page in its state.',
        'code': "history.pushState({ page: 2 }, '', '/items?page=2');",
    },
    {
        'id': 'dom-request-animation-frame',
        'language': 'dom',
        'category': 'timers',
        'title': 'requestAnimationFrame',
        'prompt': (
            'Write a `tick` that updates a position and re-schedules itself before every frame, '
            'then start it off.'
        ),
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
        'prompt': 'Dispatch an `item-added` event on the document, carrying an id in its detail.',
        'code': (
            "const event = new CustomEvent('item-added', {\n"
            '  detail: { id },\n'
            '});\n'
            'document.dispatchEvent(event);'
        ),
    },
    {
        'id': 'dom-closest',
        'language': 'dom',
        'category': 'selection',
        'title': 'closest()',
        'prompt': (
            "From an event's target, find the nearest ancestor — or the target itself — matching "
            "`.card`."
        ),
        'code': "const card = event.target.closest('.card');",
    },
]

SNIPPETS_BY_ID = {s['id']: s for s in SNIPPETS}
LANGUAGES = ('react', 'javascript', 'html', 'css', 'dom')
