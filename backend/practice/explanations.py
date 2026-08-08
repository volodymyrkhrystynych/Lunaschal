"""What each drill snippet actually is, shown under it once it has been answered.

A drill teaches the shape of a snippet by making you produce it; this file is
the other half — the definition of the thing being produced, a line per option,
field or parameter the reference uses, and what usually turns up alongside it.
Keyed by snippet id from `snippets.py` rather than stored on the snippet itself
so the bank stays readable as a bank of code; `test_every_snippet_has_an_explanation`
is the guard that the two lists never drift apart.

`parts` is a list of (name, detail) pairs rather than prose: the names are the
identifiers you just typed — `queryKey`, `queryFn`, `dependency array` — so the
explanation can be read against the code line by line instead of parsed out of a
paragraph. A speed drill ships this with the snippet; a blind drill withholds it
until the answer has been graded, since naming every field is most of the answer.
"""


def explanation_for(snippet_id: str) -> dict | None:
    """The JSON shape the Practice tab renders, or None for an unexplained id."""
    entry = EXPLANATIONS.get(snippet_id)
    if entry is None:
        return None
    return {
        'summary': entry['summary'],
        'parts': [{'name': name, 'detail': detail} for name, detail in entry['parts']],
        'related': entry.get('related', ''),
    }


EXPLANATIONS: dict[str, dict] = {
    # --- React ---
    'react-usestate': {
        'summary': (
            'useState declares one piece of component state: it returns the current value and a '
            'setter for it, and re-renders the component whenever the setter is called.'
        ),
        'parts': [
            ('useState(0)', 'The argument is the *initial* value. It is used on the first render only — every later render ignores it.'),
            ('[count, setCount]', 'Array destructuring of the returned pair, so both names are yours to pick; the convention is `x` / `setX`.'),
            ('setCount(next)', 'Queues a re-render with the new value. It does not mutate `count`: the current render keeps the value it read.'),
        ],
        'related': (
            'Updater form `setCount(c => c + 1)` when the next value depends on the previous one; '
            '`useReducer` when several fields change together; `useEffect` to react to the change.'
        ),
    },
    'react-useeffect': {
        'summary': (
            'useEffect runs a side effect *after* the render is committed to the screen — fetching, '
            'subscriptions, timers, anything that reaches outside React.'
        ),
        'parts': [
            ('the effect function', 'Runs after paint. It may return a cleanup function, which React calls before the next run and on unmount.'),
            ('[id] — the dependency array', 'Re-runs the effect only when a listed value changes. `[]` means once on mount; omitting it entirely means after *every* render.'),
        ],
        'related': (
            'A cleanup return for anything that has to be undone; `useLayoutEffect` when the work '
            'must happen before paint; an `AbortController` to cancel a fetch a re-run supersedes.'
        ),
    },
    'react-useref': {
        'summary': (
            'useRef holds a mutable value in a box that survives re-renders and, unlike state, '
            'does not cause one when written. Attached to a JSX `ref`, it holds the DOM node.'
        ),
        'parts': [
            ('useRef(null)', 'The argument is the initial `.current`. `null` is the convention for a DOM ref: nothing is attached until React commits the element.'),
            ('.current', 'The only property. Reading or writing it never re-renders — do not read it during render to decide what to draw.'),
            ('?.', 'Optional chaining, because `.current` is null on the very first render and after the element unmounts.'),
        ],
        'related': (
            '`ref={inputRef}` on the element to populate it; `useEffect` as the earliest safe place '
            'to touch the node; `forwardRef` to let a parent hand a ref through your component.'
        ),
    },
    'react-usememo': {
        'summary': (
            'useMemo caches the *result* of a computation between renders and recomputes it only '
            'when its dependencies change. It is a performance tool, not a correctness one.'
        ),
        'parts': [
            ('() => …', 'The factory. Called during render, so it must be pure and cheap enough to run at least once.'),
            ('[items]', 'Dependencies, compared by identity. A new array each render defeats the memo entirely.'),
        ],
        'related': (
            '`useCallback` for the same trick over a function; a stable dependency (memoized or '
            'from state) so the cache actually holds; `React.memo` on the child that consumes it.'
        ),
    },
    'react-usecallback': {
        'summary': (
            'useCallback returns the *same function instance* across renders until its dependencies '
            'change — so a child comparing props by identity does not re-render for nothing.'
        ),
        'parts': [
            ('() => { … }', 'The function to keep stable. It closes over the values in scope at the render it was created in.'),
            ('[id, onSelect]', 'Everything the body reads from outside itself. Omitting one leaves the callback closing over a stale value.'),
        ],
        'related': (
            '`React.memo` on the child receiving it (without that, stabilising the function buys '
            'nothing); `useMemo` for values; a ref when you want the latest value without a re-bind.'
        ),
    },
    'react-usecontext': {
        'summary': (
            'useContext reads the value of the nearest matching Provider above it, skipping the '
            'prop-drilling in between. It subscribes: a new provider value re-renders the reader.'
        ),
        'parts': [
            ('useContext(ThemeContext)', 'The argument is the context *object* itself, not its name as a string and not the provider.'),
            ('the return', 'The nearest provider\'s `value`, or the default passed to `createContext` when there is no provider above.'),
        ],
        'related': (
            '`createContext` to make the object; `<ThemeContext.Provider value={…}>` to supply it; '
            'a memoized `value` object so every consumer does not re-render each parent render.'
        ),
    },
    'react-custom-hook': {
        'summary': (
            'A custom hook is a plain function whose name starts with `use` and which calls other '
            'hooks. It packages stateful logic for reuse; each caller gets its own independent state.'
        ),
        'parts': [
            ('the `use` prefix', 'Not decoration — it is what marks the function as a hook to the linter and to readers, and what permits hook calls inside it.'),
            ('initial = false', 'A default parameter, so `useToggle()` is as valid as `useToggle(true)`.'),
            ('setValue(v => !v)', 'The updater form. Flipping from the previous value is correct even when several toggles land in one batch.'),
            ('return [value, toggle]', 'A tuple return, like the built-in hooks, so the caller can rename both at the destructuring site.'),
        ],
        'related': (
            'Returning an object instead when there are more than two or three values; a cleanup in '
            '`useEffect` for anything the hook subscribes to.'
        ),
    },
    'react-map-key': {
        'summary': (
            'Rendering a list is `array.map` to an array of elements, which JSX renders in order. '
            'The `key` is how React matches elements to data across renders.'
        ),
        'parts': [
            ('{ … }', 'Braces drop out of JSX into JavaScript. The expression evaluates to an array of elements.'),
            ('key={item.id}', 'Stable and unique among siblings. The array index works only for a list that is never reordered, inserted into or filtered.'),
            ('the arrow returning JSX', 'Parenthesised so the multi-line element is returned rather than the arrow falling into a block body that returns nothing.'),
        ],
        'related': (
            '`filter` before `map` to narrow the list; a `<>…</>` keyed with `<Fragment key>` when '
            'each entry renders two sibling elements.'
        ),
    },
    'react-conditional': {
        'summary': (
            'JSX has no `if`, so a choice between two elements is written as a ternary inside braces '
            '— an expression, because that is all JSX can interpolate.'
        ),
        'parts': [
            ('cond ? a : b', 'Both branches are elements. Swapping one for `null` renders nothing at all.'),
            ('data={data}', 'A prop: attribute syntax with braces around a JavaScript value rather than a string.'),
        ],
        'related': (
            '`{isLoading && <Spinner />}` for a one-sided condition (careful: a `0` left operand '
            'renders as "0"); an early `return` in the component for whole-body branches.'
        ),
    },
    'react-props-destructure': {
        'summary': (
            'A component takes a single `props` object; destructuring it in the parameter list names '
            'the props it uses right where the signature is read.'
        ),
        'parts': [
            ('({ title, subtitle, onClick })', 'Object destructuring in the parameter position. Names must match the prop names exactly.'),
            ('onClick={onClick}', 'Handlers are passed down as values, called by the DOM element rather than by the component.'),
        ],
        'related': (
            'Defaults inline — `({ title = \'Untitled\' })`; `...rest` to forward the leftovers onto '
            'the element; `children` for the content between the tags.'
        ),
    },
    'react-usequery': {
        'summary': (
            'useQuery declares a *read* of server state: TanStack Query fetches it, caches it under '
            'a key, dedupes concurrent callers, and re-renders you as its status changes.'
        ),
        'parts': [
            ("queryKey: ['user', id]", 'The cache identity. Every value the fetch depends on belongs in it — change the key and it fetches again; share the key and two components share one request.'),
            ('queryFn: () => api.users.get(id)', 'The fetcher. Must *return a promise* and must throw on failure — a rejected promise is what marks the query an error.'),
            ('data', 'Undefined until the first fetch resolves, then the cached value. Stays populated during a background refetch.'),
            ('isLoading', 'True only on the first load with no cached data. Use `isFetching` for "a request is in flight, cached or not".'),
        ],
        'related': (
            '`enabled` to hold the query until its inputs exist; `staleTime` to stop immediate '
            'refetches; `error` / `isError`; `useMutation` plus `invalidateQueries` for the write side.'
        ),
    },
    'react-usemutation': {
        'summary': (
            'useMutation is the write half of TanStack Query: it does not run on render, it runs '
            'when you call `.mutate()`, and it tracks that one call\'s status.'
        ),
        'parts': [
            ('useQueryClient()', 'The client the provider holds. It is how a mutation reaches the cache that queries read from.'),
            ('mutationFn: api.users.update', 'The function that performs the write. It receives whatever you pass to `.mutate(variables)` and returns a promise.'),
            ('onSuccess', 'Runs after it resolves. The result and the variables are its arguments.'),
            ("invalidateQueries({ queryKey: ['user', id] })", 'Marks matching queries stale so the ones on screen refetch — the usual way the UI catches up with a write. The key prefix-matches.'),
        ],
        'related': (
            '`.mutate(vars)` versus `.mutateAsync(vars)` when you need to await it; `isPending` to '
            'disable the button; `onError` / `onSettled`; `setQueryData` for an optimistic update.'
        ),
    },
    'react-usereducer': {
        'summary': (
            'useReducer moves state transitions out of the component and into a pure function of '
            '(state, action) → next state. Better than useState once updates come in named kinds.'
        ),
        'parts': [
            ('reducer(state, action)', 'Pure: no fetching, no mutation of `state`, same inputs always the same output. It must return a state for every action.'),
            ('action.type', 'The convention for naming the transition; the rest of the action object carries its payload.'),
            ('default: return state', 'The unknown action falls through unchanged rather than returning undefined and wiping the state.'),
            ('useReducer(reducer, { count: 0 })', 'Second argument is the initial state; the returned pair is the current state and `dispatch`.'),
        ],
        'related': (
            '`dispatch({ type: \'increment\' })` to drive it; a third lazy-init argument for '
            'expensive initial state; passing `dispatch` down through context (it is stable).'
        ),
    },
    'react-uselayouteffect': {
        'summary': (
            'useLayoutEffect has the same signature as useEffect but runs synchronously after the '
            'DOM is updated and *before* the browser paints — so a measurement it triggers is not '
            'seen as a flicker.'
        ),
        'parts': [
            ('getBoundingClientRect()', 'Reads the laid-out size and position. Forces layout, which is exactly why the read is worth doing once rather than per render.'),
            ('[]', 'Empty dependencies: measure once on mount. Add the values whose change alters the size.'),
        ],
        'related': (
            'Plain `useEffect` for everything that is not a measurement — it does not block paint; '
            '`ResizeObserver` when the size changes for reasons React never re-rendered for.'
        ),
    },
    'react-memo': {
        'summary': (
            'memo wraps a component so it re-renders only when its props change by shallow '
            'comparison, instead of every time its parent renders.'
        ),
        'parts': [
            ('memo(Component)', 'Returns a new component. It changes nothing about the output — only how often the function runs.'),
            ('the named inner function', 'Keeps a real name in stack traces and devtools instead of an anonymous wrapper.'),
            ('shallow comparison', 'Props are compared by identity, so an object, array or function rebuilt inline each render defeats it.'),
        ],
        'related': (
            '`useCallback` / `useMemo` in the parent to keep those props stable; a second argument '
            'to `memo` for a custom comparator; measuring first — most components do not need it.'
        ),
    },
    'react-forwardref': {
        'summary': (
            'forwardRef lets a component pass a ref it receives on to an element inside it, so a '
            'parent can focus or measure the real DOM node it renders.'
        ),
        'parts': [
            ('(props, ref)', 'The forwarded ref arrives as a *second parameter*, because `ref` is not part of the props object.'),
            ('ref={ref}', 'Handing it to the underlying element is what actually populates the parent\'s `.current`.'),
            ('{...props}', 'Spreads the remaining props onto the input, so `type`, `placeholder`, handlers and the rest still work.'),
        ],
        'related': (
            '`useImperativeHandle` to expose a small method surface instead of the raw node; React '
            '19, where a function component can take `ref` as a plain prop and this wrapper retires.'
        ),
    },
    'react-controlled-input': {
        'summary': (
            'A controlled input takes its displayed value from React state and reports every '
            'keystroke back, so state is the single source of truth for what is in the box.'
        ),
        'parts': [
            ('value={value}', 'What makes it controlled. React will not let the DOM drift from this value.'),
            ('onChange', 'Required alongside `value` — without it the field is frozen, since nothing ever updates the state it renders from.'),
            ('e.target.value', 'The new text, read off the element the event fired on. Always a string, even for `type="number"`.'),
        ],
        'related': (
            '`defaultValue` for the uncontrolled alternative; `checked` / `onChange` for checkboxes '
            'and radios; trimming or validating in the handler before it reaches state.'
        ),
    },
    'react-fragment': {
        'summary': (
            'A fragment groups sibling elements without adding a wrapper node to the DOM — for the '
            'cases where a `<div>` would be invalid or would break the parent\'s layout.'
        ),
        'parts': [
            ('<> … </>', 'The shorthand. Renders nothing itself; the children land directly in the parent.'),
            ('why it is needed', '`<dt>` and `<dd>` must be children of the `<dl>`, and a component may only return one root — a wrapper div would be wrong here, not merely noisy.'),
        ],
        'related': (
            '`<Fragment key={…}>` when the group is produced by a `map` and needs a key, which the '
            'shorthand cannot take; the same trick inside flex and grid containers.'
        ),
    },
    'react-children-prop': {
        'summary': (
            '`children` is the prop React fills with whatever sits between a component\'s opening '
            'and closing tags — the basis of wrapper and layout components.'
        ),
        'parts': [
            ('children', 'An ordinary prop with a reserved name. Render it where the content belongs.'),
            ('the surrounding markup', 'Everything the wrapper adds — the section, the heading — while staying ignorant of what it wraps.'),
        ],
        'related': (
            'Named "slot" props (`header`, `footer`) when there is more than one hole to fill; '
            '`React.Children` helpers for counting or mapping over them.'
        ),
    },
    'react-error-boundary': {
        'summary': (
            'An error boundary catches an exception thrown while rendering its subtree and shows a '
            'fallback instead of unmounting the whole app. It must be a class — hooks cannot do this.'
        ),
        'parts': [
            ('extends React.Component', 'The one place a class is still required; there is no hook equivalent.'),
            ('static getDerivedStateFromError()', 'Called on the throw. Returns the state patch that switches the render to the fallback. Must be pure.'),
            ('this.state.hasError', 'The flag the render branches on. Nothing resets it, so recovery means remounting the boundary or adding a reset of your own.'),
            ('this.props.children', 'The guarded subtree, rendered untouched while nothing has thrown.'),
        ],
        'related': (
            '`componentDidCatch(error, info)` for logging; a `key` on the boundary to reset it after '
            'a retry; the fact that it does *not* catch errors in event handlers or async callbacks.'
        ),
    },
    'react-portal': {
        'summary': (
            'createPortal renders children into a DOM node somewhere else in the document while '
            'keeping them in this component\'s React tree — the standard escape from `overflow` and '
            '`z-index` traps for modals and tooltips.'
        ),
        'parts': [
            ("from 'react-dom'", 'Not from `react`: it is a DOM-renderer concern, and this is the import people get wrong.'),
            ('first argument', 'The children to render.'),
            ('second argument', 'The live container node. `document.body` is the usual choice; it must exist when the portal renders.'),
        ],
        'related': (
            'Events still bubble through the React tree, not the DOM position; the native `<dialog>` '
            'element as an alternative; focus trapping, which a portal does not give you.'
        ),
    },
    'react-context-provider': {
        'summary': (
            'createContext makes a channel; the Provider sets its value for a subtree. Together they '
            'hand data to distant descendants without threading props through every level.'
        ),
        'parts': [
            ("createContext('light')", 'The argument is the default, used only where there is no Provider above the consumer — handy in tests, a bug source in production.'),
            ('<ThemeContext.Provider value="dark">', 'Overrides the default for everything inside it. Providers nest, and the nearest one wins.'),
            ('value', 'Any value. A fresh object here re-renders every consumer on each parent render, so memoize it when it is not a primitive.'),
        ],
        'related': (
            '`useContext(ThemeContext)` to read it; `useMemo` around a non-primitive value; React 19, '
            'where `<ThemeContext value>` works without `.Provider`.'
        ),
    },
    'react-use-event-listener': {
        'summary': (
            'The subscribe/unsubscribe pattern: a custom hook that adds a listener in an effect and '
            'removes it in the effect\'s cleanup, so nothing outlives the component.'
        ),
        'parts': [
            ('the returned function', 'The cleanup. React calls it before the effect re-runs and again on unmount.'),
            ('removeEventListener(event, handler)', 'Must be the *same* function reference that was added, or nothing is removed — which is why the handler is a dependency rather than an inline arrow.'),
            ('[event, handler]', 'A change to either tears the old listener down and adds the new one, in that order.'),
        ],
        'related': (
            '`useCallback` at the call site to keep `handler` stable; a ref holding the latest '
            'handler to subscribe once; `AbortController` as the modern way to remove listeners.'
        ),
    },
    'react-lazy-suspense': {
        'summary': (
            'lazy defers loading a component\'s code until it renders, and Suspense supplies what to '
            'show while that (or any other suspending read) is pending.'
        ),
        'parts': [
            ("lazy(() => import('./Settings'))", 'The factory must return a promise for a module with a *default* export. The dynamic `import()` is what makes the bundler split the chunk.'),
            ('<Suspense fallback={…}>', 'The nearest boundary above the lazy component. Without one, rendering it throws.'),
            ('fallback', 'Rendered until the chunk arrives. Give it roughly the height of the real thing to avoid a layout jump.'),
        ],
        'related': (
            'An error boundary above it, since the chunk request can fail; route-level splitting as '
            'the usual granularity; preloading on hover or intent.'
        ),
    },
    'react-useid': {
        'summary': (
            'useId generates an id that is stable across renders and unique per component instance, '
            'and that matches between server and client render — for wiring labels to controls.'
        ),
        'parts': [
            ('useId()', 'Takes no arguments. Do not use it for list keys: it identifies a component instance, not a data row.'),
            ('htmlFor / id', 'The pairing that makes clicking the label focus the input and lets a screen reader announce the name.'),
            ('why not a literal', 'A hardcoded id collides as soon as the component is rendered twice on one page.'),
        ],
        'related': (
            'One id plus suffixes (`${id}-error`) for `aria-describedby`; `aria-label` when there is '
            'no visible label to point at.'
        ),
    },
    'react-usetransition': {
        'summary': (
            'useTransition marks a state update as non-urgent, so React can keep the interface '
            'responsive to typing and clicking while the expensive re-render it triggers proceeds.'
        ),
        'parts': [
            ('startTransition(fn)', 'Updates queued inside the callback are the low-priority ones. The call must be synchronous — updates after an `await` are not included.'),
            ('isPending', 'True while the transition renders, for a subtle busy hint rather than a spinner that replaces the old content.'),
            ('what stays urgent', 'The controlled input\'s own `setValue` — keep it *outside* the transition, or the field lags behind the keys.'),
        ],
        'related': (
            '`useDeferredValue` for the same effect driven from a value rather than a setter; '
            '`useMemo` over the filtering itself; Suspense, which transitions cooperate with.'
        ),
    },
    'react-conditional-classname': {
        'summary': (
            'A className is just a string, so conditional styling is string building in a braces '
            'expression — a template literal with a ternary per optional class.'
        ),
        'parts': [
            ('`btn ${…}`', 'A template literal, inside JSX braces. Note the space before the interpolation: without it the two classes fuse into one.'),
            ("isActive ? 'btn-active' : ''", 'The empty alternative is deliberate — `false` or `undefined` would print as text in a template literal.'),
        ],
        'related': (
            '`clsx` / `classnames` once there are several conditions; an array joined with a space; '
            '`data-*` attributes styled from CSS as the alternative to class juggling.'
        ),
    },
    # --- JavaScript ---
    'js-for-loop': {
        'summary': (
            'The classic indexed loop: an initialiser, a condition checked before each pass, and an '
            'update run after it. Still the tool of choice when the index itself matters.'
        ),
        'parts': [
            ('let i = 0', 'Runs once. `let` (not `var`) scopes `i` to the loop and gives each iteration its own binding, which closures inside the body depend on.'),
            ('i < items.length', 'Checked before every pass, so an empty array runs the body zero times.'),
            ('i++', 'Runs after each pass. Forgetting it is the classic infinite loop.'),
            ('items[i]', 'Index access. Out of range is `undefined`, not an error.'),
        ],
        'related': (
            '`for...of` when the index is unused; `break` and `continue`; counting down or stepping '
            'by two, which only this form expresses cleanly.'
        ),
    },
    'js-for-of': {
        'summary': (
            'for...of iterates the *values* of anything iterable — arrays, strings, Maps, Sets, '
            'NodeLists — with no index bookkeeping.'
        ),
        'parts': [
            ('const item', 'A fresh binding per iteration, so `const` is correct and a closure over it captures that one value.'),
            ('of', 'Values. `for...in` walks *keys* instead, including inherited ones, and is the wrong loop for an array.'),
        ],
        'related': (
            '`entries()` for index and value together; `break` / `continue`, which `forEach` cannot '
            'do; `for await...of` over an async iterable.'
        ),
    },
    'js-map-filter-reduce': {
        'summary': (
            'The three iteration primitives, chained: filter narrows, map transforms, reduce folds '
            'the result into one value. Each returns a new array and mutates nothing.'
        ),
        'parts': [
            ('.filter(pred)', 'Keeps the entries the predicate returns truthy for.'),
            ('.map(fn)', 'Same length as its input, each entry replaced by the return value.'),
            ('.reduce((sum, price) => …, 0)', 'The accumulator plus the current entry. The trailing `0` is the initial value — omit it on an empty array and it throws.'),
        ],
        'related': (
            'One `reduce` when the chain gets long enough to care about the intermediate arrays; '
            '`flatMap` for a map that emits several entries; `some` / `every` for boolean answers.'
        ),
    },
    'js-arrow-function': {
        'summary': (
            'An arrow function is a shorter function expression whose body, when it is a single '
            'expression, is also its return value. It has no `this` of its own.'
        ),
        'parts': [
            ('(a, b) =>', 'The parameter list. Parentheses are optional for exactly one parameter, required for none or several.'),
            ('a + b', 'A concise body: the expression is returned. Adding braces makes it a block, and then `return` is on you.'),
            ('no own `this`', '`this` comes from the enclosing scope — which is why arrows are the right callback and the wrong object method.'),
        ],
        'related': (
            '`({ id })` — an object literal body needs wrapping parentheses or the braces read as a '
            'block; default and rest parameters; the absence of `arguments` inside an arrow.'
        ),
    },
    'js-array-destructure': {
        'summary': (
            'Array destructuring binds names to entries by position, with a rest element to collect '
            'whatever is left over.'
        ),
        'parts': [
            ('[first, second]', 'By position, not by name. A hole — `[, second]` — skips an entry.'),
            ('...rest', 'A new array of the remainder. It must come last, and there can only be one.'),
            ('missing entries', 'Bind to `undefined` rather than throwing; `= fallback` per name supplies a default.'),
        ],
        'related': (
            'Swapping with `[a, b] = [b, a]`; destructuring the pair a hook or `Object.entries` '
            'returns; object destructuring when order is not meaningful.'
        ),
    },
    'js-object-destructure': {
        'summary': (
            'Object destructuring binds names to properties by key, with a rest object for the '
            'properties you did not name.'
        ),
        'parts': [
            ('{ id, name }', 'By key. `{ id: userId }` renames on the way out.'),
            ('...rest', 'A new object holding the remaining own enumerable properties — the usual way to drop a field without mutating.'),
            ('absent keys', '`undefined`, unless a `= default` is given. Destructuring `null` or `undefined` itself throws.'),
        ],
        'related': (
            'Destructuring in a parameter list; nested patterns; `...rest` for forwarding leftover '
            'props onto a DOM element.'
        ),
    },
    'js-template-literal': {
        'summary': (
            'Backtick strings interpolate expressions with `${…}` and keep literal newlines, so '
            'building a sentence needs no concatenation.'
        ),
        'parts': [
            ('`…`', 'Backticks, not quotes. Newlines inside are part of the string.'),
            ('${name}', 'Any expression, not just a variable, coerced to a string. Beware: `null` prints as "null".'),
        ],
        'related': (
            'Multi-line strings; tagged templates (`` sql`…` ``); a plain quoted string when there '
            'is nothing to interpolate.'
        ),
    },
    'js-async-await': {
        'summary': (
            'async/await writes promise-based code in a straight line: `await` pauses the function '
            'until the promise settles, and try/catch is how a rejection is handled.'
        ),
        'parts': [
            ('async function', 'Always returns a promise, whatever the body returns. It is the only place `await` is allowed (bar top-level modules).'),
            ('await fetch(...)', 'Yields the resolved value. Note that fetch resolves for a 404 too — check `res.ok` for a real error path.'),
            ('await res.json()', 'The body arrives separately from the headers, so parsing is a second await.'),
            ('catch (err)', 'Catches a rejection of anything awaited inside the try, exactly like a thrown error.'),
        ],
        'related': (
            '`res.ok` and an explicit throw; `finally` for cleanup; `Promise.all` for concurrency; '
            'returning versus swallowing the error, which a bare `console.error` quietly does.'
        ),
    },
    'js-fetch': {
        'summary': (
            'The promise-chaining form of the same fetch: each `.then` receives the previous step\'s '
            'resolved value and returns the next promise in the chain.'
        ),
        'parts': [
            ('fetch(url)', 'Returns a promise for the *response*, resolved as soon as the headers arrive.'),
            ('.then(res => res.json())', 'Returning a promise from a `then` flattens it, so the next `then` gets the parsed body rather than a promise.'),
            ('.then(data => …)', 'The parsed JSON. Each callback runs asynchronously, after the current task finishes.'),
        ],
        'related': (
            '`.catch` for rejections and `.finally` for cleanup; `res.ok`, since a 500 still '
            'resolves; async/await for the same thing read top-to-bottom.'
        ),
    },
    'js-spread': {
        'summary': (
            'Object spread copies own enumerable properties into a new object — the standard way to '
            'merge defaults with overrides without mutating either.'
        ),
        'parts': [
            ('order', 'Later wins. `{ ...overrides, ...defaults }` would let the defaults clobber the overrides.'),
            ('shallow', 'Nested objects are shared by reference, not cloned — mutating one afterwards is felt by both.'),
            ('undefined values', 'A key present with value `undefined` still overrides. Filter it out if that is not what you mean.'),
        ],
        'related': (
            '`structuredClone` for a deep copy; `Object.assign(target, …)` when mutating on purpose; '
            'array spread, which concatenates rather than merges.'
        ),
    },
    'js-optional-chaining': {
        'summary': (
            'Optional chaining short-circuits to `undefined` the moment a link in the path is null '
            'or undefined, instead of throwing — usually paired with a default.'
        ),
        'parts': [
            ('?.', 'Guards the *left* side. It stops the whole rest of the chain, so one `?.` covers everything after it.'),
            ('?? \'Unknown\'', 'Supplies the fallback for the `undefined` the chain produced.'),
            ('what it does not do', 'It does not guard against a *typo* — a misspelt key is also undefined, silently.'),
        ],
        'related': (
            '`obj.method?.()` for an optional call and `arr?.[i]` for an index; `??` versus `||`; '
            'destructuring with defaults for the same job on a known shape.'
        ),
    },
    'js-nullish-coalescing': {
        'summary': (
            '`??` supplies a fallback only when the left side is null or undefined — unlike `||`, '
            'which also fires on 0, "" and false.'
        ),
        'parts': [
            ('??', 'Nullish, not falsy. This is the whole point: a `pageSize` of 0 is kept, where `|| 20` would replace it.'),
            ('short-circuit', 'The right side is evaluated only if needed, so an expensive default costs nothing in the common case.'),
        ],
        'related': (
            '`??=` to assign only when nullish; `?.` in front of it; the syntax error you get from '
            'mixing `??` with `&&`/`||` unparenthesised.'
        ),
    },
    'js-array-find': {
        'summary': (
            'find returns the first entry the predicate accepts, or `undefined` when none do — for '
            'when you want the item rather than a filtered array.'
        ),
        'parts': [
            ('the predicate', 'Receives (value, index, array). It stops at the first truthy result.'),
            ('undefined on no match', 'Which is why the result is usually guarded before use, not dereferenced straight away.'),
            ('===', 'Strict comparison: no type coercion, which is what you want for a string tag.'),
        ],
        'related': (
            '`findIndex` for the position, `findLast` from the end; `filter` for every match; '
            '`some` when the answer is really just yes or no.'
        ),
    },
    'js-array-some-every': {
        'summary': (
            'Two boolean folds: `some` is "is there at least one", `every` is "are they all". Both '
            'stop as soon as the answer is settled.'
        ),
        'parts': [
            ('some', 'False for an empty array — nothing there to satisfy it.'),
            ('every', 'True for an empty array, vacuously. This surprises people, so it is worth knowing before relying on it.'),
        ],
        'related': (
            '`find` when you want the matching entry too; `filter(…).length > 0`, which does the '
            'same thing slower; `includes` for a plain value check.'
        ),
    },
    'js-set-dedup': {
        'summary': (
            'A Set holds unique values, so round-tripping an array through one drops the duplicates '
            '— the shortest correct dedupe there is.'
        ),
        'parts': [
            ('new Set(values)', 'Uniqueness is by `SameValueZero`, so it dedupes primitives but not two objects with equal contents.'),
            ('[...set]', 'Spread turns the Set back into an array, in first-seen insertion order.'),
        ],
        'related': (
            '`Array.from(set)` as the same conversion; `set.has(x)`, which is O(1) against '
            '`array.includes`; a Map keyed by id to dedupe objects.'
        ),
    },
    'js-map-usage': {
        'summary': (
            'A Map is a keyed collection that takes *any* key type, keeps insertion order, and knows '
            'its own size — a real dictionary, where a plain object is a struct.'
        ),
        'parts': [
            ('new Map()', 'Optionally seeded with an array of `[key, value]` pairs.'),
            ('.set(key, value)', 'Returns the Map, so calls chain.'),
            ('.get(key)', '`undefined` when absent — use `.has(key)` to tell that apart from a stored undefined.'),
        ],
        'related': (
            '`.size`, `.delete`, `.forEach` and iteration with `for...of`; objects as keys, which is '
            'the thing a plain object cannot do; `WeakMap` when the keys should not be pinned.'
        ),
    },
    'js-promise-all': {
        'summary': (
            'Promise.all awaits several promises concurrently and resolves to an array of their '
            'results in the order given — not the order they finished.'
        ),
        'parts': [
            ('the array argument', 'The calls are started *before* the await, which is what makes them overlap. Awaiting each in turn would run them one after the other.'),
            ('[user, posts]', 'Positional destructuring of the results array.'),
            ('failure', 'Rejects as soon as any one of them does, and the rest keep running unwatched.'),
        ],
        'related': (
            '`Promise.allSettled` when a partial result is still useful; `Promise.race` / `any`; a '
            'try/catch around the await.'
        ),
    },
    'js-debounce': {
        'summary': (
            'Debouncing collapses a burst of calls into one: each call resets a timer, and the '
            'wrapped function runs only once the calls stop for `delay` milliseconds.'
        ),
        'parts': [
            ('let timer', 'Kept in the closure, so every returned wrapper has its own — do not hoist it to module scope.'),
            ('clearTimeout(timer)', 'The reset. This is the line that makes it a debounce rather than a delay.'),
            ('(...args) => fn(...args)', 'Rest then spread, so the wrapper is transparent to whatever arguments the caller passes.'),
        ],
        'related': (
            'Throttle, which fires at a steady rate instead of at the end; a leading-edge variant; '
            'clearing the timer on unmount so a pending call cannot fire into a dead component.'
        ),
    },
    'js-class': {
        'summary': (
            'A class declares a constructor and its methods in one block; `new` allocates the '
            'instance and runs the constructor with `this` bound to it.'
        ),
        'parts': [
            ('constructor(width, height)', 'Runs on `new`. Assigning to `this.x` is what creates the instance fields.'),
            ('get area()', 'A getter: read as `rect.area`, with no parentheses, but computed on each access.'),
            ('methods', 'Live on the prototype — shared by every instance rather than copied per object.'),
        ],
        'related': (
            '`extends` and `super(...)`; `static` members; `#private` fields; class fields as a '
            'shorter way to set defaults.'
        ),
    },
    'js-default-params': {
        'summary': (
            'A default parameter supplies a value when the argument is missing or `undefined`, '
            'moving the fallback into the signature where it can be read.'
        ),
        'parts': [
            ("name = 'friend'", 'Applies to `undefined` only. Passing `null` overrides the default with null.'),
            ('evaluated per call', 'So `= []` gives a fresh array each call, and a default may refer to earlier parameters.'),
        ],
        'related': (
            'Destructured defaults — `({ size = 10 } = {})`; `??` for the same fallback further '
            'inside the body; the fact that defaulted parameters do not count toward `fn.length`.'
        ),
    },
    'js-rest-params': {
        'summary': (
            'A rest parameter gathers every remaining argument into a real array, so a function can '
            'take any number of them.'
        ),
        'parts': [
            ('...numbers', 'A genuine array — `map`, `reduce` and the rest work on it. It must be the last parameter.'),
            ('reduce((a, b) => a + b, 0)', 'The `0` makes `sum()` with no arguments return 0 instead of throwing.'),
        ],
        'related': (
            'Spread at the call site, `sum(...values)`, which is the mirror image; the older '
            '`arguments` object, which is not an array and does not exist in arrows.'
        ),
    },
    'js-switch': {
        'summary': (
            'switch compares one value against a list of cases with strict equality, and runs from '
            'the matching label onward — which is why each branch ends in a return or a break.'
        ),
        'parts': [
            ('case', 'Matched with `===`. No coercion, so `case 1` never matches `"1"`.'),
            ('return / break', 'Without one, execution falls through into the next case. Sometimes intended, usually a bug.'),
            ('default', 'The catch-all. Placing it last is convention, not a requirement.'),
        ],
        'related': (
            'An object lookup as the flatter alternative for pure value mapping; grouped fall-through '
            'cases on purpose; block braces in a case when it declares `let`.'
        ),
    },
    'js-try-catch-finally': {
        'summary': (
            'try/catch/finally: run something that might throw, handle the throw, and run the '
            'cleanup either way.'
        ),
        'parts': [
            ('try', 'Only *synchronous* throws inside it are caught — a rejected promise that is not awaited escapes.'),
            ('catch (err)', 'The binding can be omitted entirely (`catch {}`) when the error is not used.'),
            ('finally', 'Runs on success, on throw, and on an early `return` from the try — which is what makes it the right home for a flag reset.'),
        ],
        'related': (
            'Rethrowing after logging so the caller still learns of it; `await` inside try for the '
            'async version; error subclasses to branch on the kind of failure.'
        ),
    },
    'js-json-parse-stringify': {
        'summary': (
            'localStorage stores strings only, so a structured value round-trips through '
            'JSON.stringify on the way in and JSON.parse on the way out.'
        ),
        'parts': [
            ('JSON.stringify(entry)', 'Serialises. It silently drops functions and `undefined`, and turns a Date into a string.'),
            ('JSON.parse(text)', 'Throws a SyntaxError on malformed input — worth a try/catch when the data came from storage written by an older version.'),
            ('getItem', 'Returns `null` for a key that was never set, and `JSON.parse(null)` gives `null` rather than throwing.'),
        ],
        'related': (
            'A `replacer` / `reviver` argument for custom types; `structuredClone` for cloning '
            'without the string detour; the storage quota, which throws when exceeded.'
        ),
    },
    'js-array-sort': {
        'summary': (
            'sort orders an array *in place*, so a copy is taken first; the comparator is what makes '
            'it order numbers correctly rather than lexicographically.'
        ),
        'parts': [
            ('[...items]', 'The copy. Without it the caller\'s array is reordered underneath them.'),
            ('(a, b) => a.price - b.price', 'Negative keeps `a` first, positive swaps, zero ties. Reverse the subtraction for descending.'),
            ('the default sort', 'Compares string forms — which puts 10 before 9. That is the bug the comparator exists to avoid.'),
        ],
        'related': (
            '`toSorted()` for the non-mutating version; `localeCompare` for text; chained '
            'comparators for a secondary key; sort stability, which is guaranteed.'
        ),
    },
    'js-flat-flatmap': {
        'summary': (
            'flatMap maps and then flattens one level — the idiom for "each entry produces several" '
            '— while flat flattens an existing nested array to a given depth.'
        ),
        'parts': [
            ('flatMap(fn)', 'One level of flattening only, and exactly equivalent to `.map(fn).flat()`.'),
            ('flat(2)', 'The depth argument. The default is 1; `Infinity` flattens all the way down.'),
        ],
        'related': (
            'Returning `[]` from flatMap to drop an entry (map plus filter in one pass); '
            '`new Set` afterwards to dedupe the collected tags.'
        ),
    },
    # --- HTML ---
    'html-boilerplate': {
        'summary': (
            'The minimum a browser needs to render a page in standards mode: a doctype, the root '
            'element, a head of metadata and a body of content.'
        ),
        'parts': [
            ('<!DOCTYPE html>', 'Not a tag — the switch out of quirks mode. It must be the very first thing in the file.'),
            ('lang="en"', 'Tells screen readers which pronunciation to use and translation tools what they are looking at.'),
            ('<meta charset="UTF-8">', 'Must appear within the first 1024 bytes, so it goes first in the head. Without it, non-ASCII text can be mis-decoded.'),
            ('<title>', 'The tab label, the bookmark name and the first thing a screen reader announces.'),
        ],
        'related': (
            'The viewport meta tag next to the charset; `<meta name="description">`; stylesheet '
            'links in the head and scripts at the end of the body (or `defer`).'
        ),
    },
    'html-form-input': {
        'summary': (
            'A labelled, validated text field: the label names the control for everyone, and the '
            'type and `required` attributes get validation from the browser for free.'
        ),
        'parts': [
            ('for="email" / id="email"', 'The pairing. Clicking the label focuses the input, and assistive tech reads the label as the field\'s name.'),
            ('type="email"', 'Gets an @-shaped keyboard on mobile and a format check on submit.'),
            ('name="email"', 'The key the value is submitted under. No name, no submitted value — separate from `id`, which is for linking.'),
            ('required', 'A boolean attribute: its presence is the value. Blocks submission while empty.'),
        ],
        'related': (
            '`placeholder` (a hint, never a label substitute); `autocomplete="email"`; `pattern` and '
            '`:invalid` styling; wrapping the input inside the label as the alternative to `for`.'
        ),
    },
    'html-semantic-layout': {
        'summary': (
            'The three page-level landmarks. Semantic elements carry the same default styling as a '
            'div but announce their role, giving assistive tech a map to jump around.'
        ),
        'parts': [
            ('<header>', 'Introductory content. At the top level it is the page banner; inside an article it is that article\'s header.'),
            ('<main>', 'The unique, page-specific content. Exactly one per page — it is what "skip to content" skips to.'),
            ('<footer>', 'Closing content for its nearest sectioning ancestor.'),
        ],
        'related': (
            '`<nav>` and `<aside>` as the other landmarks; `<section>` and `<article>` inside main; '
            'a heading order that matches the structure.'
        ),
    },
    'html-anchor-img': {
        'summary': (
            'A linked image: an `<img>` as the anchor\'s content, opening in a new tab without '
            'handing that tab a reference back to this page.'
        ),
        'parts': [
            ('href', 'The destination. It is what makes the element focusable and openable in a new tab; a clickable div gets none of that.'),
            ('target="_blank"', 'Opens in a new tab.'),
            ('rel="noopener"', 'The safety half: without it the opened page can reach back through `window.opener`. Modern browsers imply it, but stating it is still the habit.'),
            ('alt="A photo"', 'The link\'s accessible name here, since the image *is* the link text. Never empty in this position.'),
        ],
        'related': (
            '`rel="noreferrer"` alongside it; `width` / `height` to reserve space; `loading="lazy"` '
            'for below-the-fold images; `download` for a file link.'
        ),
    },
    'html-table': {
        'summary': (
            'A data table, sectioned. The head/body split is what lets a screen reader announce the '
            'column a cell belongs to as it moves across a row.'
        ),
        'parts': [
            ('<thead> / <tbody>', 'Structure, not decoration: they mark which rows are headings and which are data. `<tbody>` is implied but written out for clarity.'),
            ('<tr>', 'A row. Every cell lives inside one.'),
            ('<th>', 'A header cell — bold and centred by default, and read as the label for its column.'),
            ('<td>', 'A data cell.'),
        ],
        'related': (
            '`scope="col"` / `scope="row"` for unambiguous association; `<caption>` as the table\'s '
            'title; `<tfoot>`; `colspan` / `rowspan`; not using tables for layout.'
        ),
    },
    'html-list': {
        'summary': (
            'An unordered list: a container plus one `<li>` per item. Screen readers announce the '
            'item count, which is the reason to use it over stacked divs.'
        ),
        'parts': [
            ('<ul>', 'Unordered — the sequence carries no meaning. Only `<li>` (and script/template) may be its direct children.'),
            ('<li>', 'One item. It may hold any content, including nested lists.'),
        ],
        'related': (
            '`<ol>` when the order matters, with `start` and `value`; `<dl>` / `<dt>` / `<dd>` for '
            'term-definition pairs; `list-style: none` for a nav built from a list.'
        ),
    },
    'html-button': {
        'summary': (
            'A submit button: inside a form it submits it, and unlike a clickable div it is '
            'focusable, keyboard-activated and announced as a button.'
        ),
        'parts': [
            ('type="submit"', 'The default inside a form, written out because the default is easy to forget — the common bug is a button meant to do nothing reloading the page.'),
            ('class="btn-primary"', 'A styling hook only; it carries no behaviour.'),
            ('the text content', 'The accessible name. An icon-only button needs `aria-label` instead.'),
        ],
        'related': (
            '`type="button"` for anything that is not submitting; `disabled`; `form` to submit a '
            'form the button sits outside of; `<a>` when it navigates rather than acts.'
        ),
    },
    'html-select': {
        'summary': (
            'A dropdown: the select is the control and carries the name, each option carries the '
            'value that would be submitted.'
        ),
        'parts': [
            ('name="color"', 'The submitted key. The submitted value is the chosen option\'s `value`.'),
            ('value', 'What is sent. Without it the option\'s text is sent instead, which couples your data to your wording.'),
            ('the option text', 'What is shown.'),
        ],
        'related': (
            '`selected` for the initial choice; `multiple` and `size`; `<optgroup>` for grouping; '
            '`disabled` on a placeholder first option; a radio group when there are only two or three.'
        ),
    },
    'html-meta-viewport': {
        'summary': (
            'The tag that stops a phone rendering the page at desktop width and zooming out. '
            'Responsive CSS does nothing on mobile without it.'
        ),
        'parts': [
            ('width=device-width', 'Makes the layout viewport the device\'s own width, so media queries see real values.'),
            ('initial-scale=1.0', 'Starts at 100% zoom rather than scaled to fit.'),
        ],
        'related': (
            'Not using `user-scalable=no` or a locked `maximum-scale`, which blocks pinch zoom; '
            '`viewport-fit=cover` plus the `safe-area-inset-*` variables on notched screens.'
        ),
    },
    'html-video': {
        'summary': (
            'An embedded video with the browser\'s own player, its file given by a nested `<source>` '
            'so alternative formats can be listed.'
        ),
        'parts': [
            ('controls', 'A boolean attribute. Without it there is no play button at all unless you build one.'),
            ('poster="thumb.jpg"', 'The still shown before playback, and while the video loads.'),
            ('<source src type>', 'The candidate file. The browser picks the first `type` it can play, which is why this is nested rather than an attribute.'),
        ],
        'related': (
            '`muted` plus `autoplay` (the only autoplay browsers allow); `loop`, `playsinline`, '
            '`preload`; `<track kind="captions">`; fallback text between the tags.'
        ),
    },
    'html-audio': {
        'summary': (
            'An audio player. With a single format there is no need for nested sources — `src` on '
            'the element is enough.'
        ),
        'parts': [
            ('controls', 'Again, no player UI without it.'),
            ('src', 'The shorthand form. Use nested `<source>` elements when you need to offer more than one format.'),
            ('the closing tag', 'Required: audio is not a void element, and the content inside it is the fallback.'),
        ],
        'related': (
            '`preload="none"` to skip the download until play; `loop`; `new Audio()` in JS for '
            'sounds with no visible player.'
        ),
    },
    'html-details-summary': {
        'summary': (
            'A native disclosure widget — collapsible content with keyboard support and no '
            'JavaScript. The summary is the always-visible label and the toggle.'
        ),
        'parts': [
            ('<details>', 'The container. Add the `open` attribute to start expanded; the browser adds and removes it as the user toggles.'),
            ('<summary>', 'Must be the first child. Everything after it is the collapsible content.'),
        ],
        'related': (
            'The `toggle` event for reacting to it; `name` on several details to make them an '
            'accordion; `::marker` and `[open]` for styling; find-in-page, which can auto-expand it.'
        ),
    },
    'html-dialog': {
        'summary': (
            'The native modal: `showModal()` gives it a backdrop, a focus trap, Escape-to-close and '
            'inert page content underneath, none of which you have to write.'
        ),
        'parts': [
            ('<dialog id>', 'Hidden until opened. The id is how the script reaches it.'),
            ('showModal() vs show()', 'Modal gets the backdrop and the focus trap; `show()` is a non-modal popover-ish panel.'),
            ('autofocus', 'Where focus lands on open. Without it focus goes to the dialog itself, which is rarely what you want.'),
        ],
        'related': (
            '`close(returnValue)` and the `close` event; `::backdrop` styling; `method="dialog"` on '
            'a form inside it; the `popover` attribute for non-modal overlays.'
        ),
    },
    'html-progress': {
        'summary': (
            'A determinate progress bar: how far along a task is, drawn by the browser and announced '
            'as a progress indicator.'
        ),
        'parts': [
            ('value', 'The current amount. Omit it entirely for the indeterminate "working…" style.'),
            ('max', 'The total. It defaults to 1, so `value="70"` without a max is pinned at full.'),
        ],
        'related': (
            '`<meter>` for a measurement within a range (disk usage, a score) rather than a task; '
            '`aria-label` to say what is progressing; `::-webkit-progress-bar` for styling.'
        ),
    },
    'html-datalist': {
        'summary': (
            'Autocomplete suggestions for a free-text input: the user may pick one or type something '
            'else entirely, which is what separates it from a select.'
        ),
        'parts': [
            ('list="browsers"', 'Points at the datalist\'s `id`. This attribute is the whole wiring.'),
            ('<datalist id>', 'Never rendered itself; only its options surface, as the input\'s dropdown.'),
            ('<option value>', 'Suggestions. The `value` is what gets inserted into the field.'),
        ],
        'related': (
            'A `<select>` when the value must be one of the listed ones; `autocomplete` for '
            'browser-remembered values; server-driven suggestions, which need a real combobox.'
        ),
    },
    'html-textarea': {
        'summary': (
            'A multi-line text field. Its value is its *content*, not a `value` attribute — the one '
            'way it differs from every other input.'
        ),
        'parts': [
            ('rows="4"', 'The visible height in lines. It is a starting size, not a limit on what can be typed.'),
            ('id + for', 'The same label pairing as any other control.'),
            ('the closing tag', 'Required, and whitespace between the tags becomes the initial value — so keep it empty.'),
        ],
        'related': (
            '`maxlength`, `placeholder`, `name`; `resize` in CSS; `wrap="soft"` / `"hard"`; growing '
            'the box with `field-sizing: content`.'
        ),
    },
    'html-fieldset': {
        'summary': (
            'A fieldset groups related controls and the legend captions the group, so a screen reader '
            'announces "Shipping address, Street" rather than a bare "Street".'
        ),
        'parts': [
            ('<fieldset>', 'The grouping. Setting `disabled` on it disables every control inside at once.'),
            ('<legend>', 'Must be the first child. It is the group\'s accessible name.'),
        ],
        'related': (
            'A fieldset per radio group, which is where it matters most; `role="group"` plus '
            '`aria-labelledby` when fieldset styling gets in the way.'
        ),
    },
    'html-radio-group': {
        'summary': (
            'Radios that share a `name` form one mutually exclusive group; the shared name is the '
            'grouping, and each radio carries its own value and label.'
        ),
        'parts': [
            ('name="theme"', 'Identical across the group. Different names give you two independent, separately-checkable radios — the classic bug.'),
            ('value', 'What is submitted for the chosen one. Only the checked radio is submitted at all.'),
            ('id + for', 'Per radio, so each has its own clickable label.'),
        ],
        'related': (
            '`checked` on a sensible default; a `<fieldset>` with a `<legend>` around the group; '
            'arrow-key navigation, which the browser provides once the names match.'
        ),
    },
    'html-checkbox': {
        'summary': (
            'A single boolean control. It submits only when checked, and it submits `on` unless you '
            'give it a value.'
        ),
        'parts': [
            ('type="checkbox"', 'Independent of any other checkbox, even with a shared name (which submits several values).'),
            ('id + for', 'The label pairing — which also gives you a much larger tap target.'),
            ('name', 'The submitted key. Absent from the submission entirely when unchecked, rather than sent as false.'),
        ],
        'related': (
            '`checked`, `value`; the `indeterminate` property, settable only from JS; `:checked` in '
            'CSS; a radio group when the options are exclusive.'
        ),
    },
    'html-figure': {
        'summary': (
            'A figure ties self-contained content to its caption, so the association survives being '
            'moved and is announced as a unit.'
        ),
        'parts': [
            ('<figure>', 'The wrapper for content referenced from the main text — an image, a chart, a code sample.'),
            ('<figcaption>', 'The caption. It must be the first or last child; it describes the figure to everyone.'),
            ('alt vs figcaption', 'Different jobs: `alt` replaces the image for someone who cannot see it, the caption is read by everyone. Do not duplicate one into the other.'),
        ],
        'related': (
            '`<picture>` inside it for responsive sources; `aria-labelledby` for the same tie '
            'between arbitrary elements.'
        ),
    },
    'html-nav': {
        'summary': (
            'A navigation landmark holding links. The element is the semantics; the links are the '
            'navigation.'
        ),
        'parts': [
            ('<nav>', 'For major link blocks, not every group of links. Several per page is fine when each is labelled.'),
            ('href="/"', 'A root-relative path — resolved from the site root regardless of the current URL.'),
        ],
        'related': (
            '`aria-label` on each nav when there is more than one; `aria-current="page"` on the '
            'active link; a `<ul>` of `<li>`s inside for the item count.'
        ),
    },
    'html-article-section': {
        'summary': (
            'Article is content that stands on its own; section is a thematic grouping within '
            'something. Both take a heading — that is what makes them meaningful rather than divs.'
        ),
        'parts': [
            ('<article>', 'Independently distributable: a post, a comment, a card. It makes sense pulled out of the page.'),
            ('<section>', 'A part of a whole, and it should have a heading. Without one, a `<div>` is the honest choice.'),
            ('<h2>', 'The section\'s title. Heading level comes from the document outline, not from the nesting depth.'),
        ],
        'related': (
            '`aria-labelledby` pointing at the heading to name the region; `<div>` for grouping that '
            'is purely for layout; `<header>` / `<footer>` inside an article.'
        ),
    },
    'html-iframe': {
        'summary': (
            'An iframe embeds another document in a nested browsing context — a separate page with '
            'its own scripts and origin, rendered inline.'
        ),
        'parts': [
            ('src', 'The embedded URL. The remote page may refuse with `X-Frame-Options` / CSP.'),
            ('title', 'Required for accessibility: it is the frame\'s accessible name, and a screen reader has nothing else to announce.'),
            ('loading="lazy"', 'Defers loading until it is near the viewport — worth a lot, since an iframe loads an entire page.'),
        ],
        'related': (
            '`sandbox` to strip its privileges; `allow` for camera and the like; `referrerpolicy`; '
            '`postMessage` for talking to it across origins.'
        ),
    },
    'html-data-attribute': {
        'summary': (
            'Any `data-*` attribute is valid HTML and reserved for your own use — the sanctioned way '
            'to attach state to an element without inventing attributes.'
        ),
        'parts': [
            ('data-id="42"', 'Always a string in the DOM. Convert it before doing arithmetic.'),
            ('data-status="done"', 'Reads nicely from CSS too: `li[data-status="done"] { … }`.'),
            ('naming', 'Lower-case with hyphens in HTML; the hyphens become camelCase in `dataset` — `data-user-id` is `dataset.userId`.'),
        ],
        'related': (
            '`element.dataset` for reading and writing; attribute selectors for styling from state; '
            '`aria-*` when the information is about accessibility rather than yours.'
        ),
    },
    'html-aria-label': {
        'summary': (
            'aria-label gives an element an accessible name when there is no visible text to serve '
            'as one — the icon-button case.'
        ),
        'parts': [
            ('aria-label', 'Overrides the element\'s own content as its name. It is announced but never displayed.'),
            ('&times;', 'A character entity for ×. Decorative glyphs make terrible names, which is exactly why the label is needed.'),
            ('when not to', 'If there is visible text, label it with that instead — a mismatch between what is seen and what is announced is its own bug.'),
        ],
        'related': (
            '`aria-labelledby` to point at existing text; `aria-describedby` for supplementary help; '
            'visually-hidden text as the CSS-based alternative.'
        ),
    },
    'html-picture-srcset': {
        'summary': (
            'Picture lets the browser choose between *different images* by condition — art direction '
            '— with the `<img>` as both the fallback and the thing actually rendered.'
        ),
        'parts': [
            ('<source srcset media>', 'A candidate plus the condition it applies under. The first matching source wins, so order is significant.'),
            ('<img>', 'Mandatory and last. It is used when no source matches, and it carries the `alt`, sizing and loading attributes.'),
            ('picture vs srcset', 'Use `<picture>` when the images differ (crop, aspect); use `srcset`/`sizes` on a plain `img` when it is the same image at different resolutions.'),
        ],
        'related': (
            '`sizes` and `w` descriptors; `type="image/webp"` sources with a JPEG fallback; '
            '`width` / `height` to prevent layout shift.'
        ),
    },
    # --- CSS ---
    'css-flex-center': {
        'summary': (
            'The canonical centring: a flex container aligns its children on both axes with two '
            'properties, no magic numbers and no knowledge of the child\'s size.'
        ),
        'parts': [
            ('display: flex', 'Turns the element into a flex container. Its children become flex items, laid out in a row by default.'),
            ('justify-content: center', 'Along the *main* axis — horizontal here, because the direction is a row.'),
            ('align-items: center', 'Along the *cross* axis — vertical here. Swap `flex-direction` and the two exchange meanings.'),
        ],
        'related': (
            '`flex-direction`, `gap`; `place-items: center` on a grid for the same result in one '
            'line; `align-self` to override a single item.'
        ),
    },
    'css-grid-template': {
        'summary': (
            'A grid with explicit columns: unlike flexbox, the tracks are declared on the container, '
            'so the children line up in both directions.'
        ),
        'parts': [
            ('display: grid', 'Children become grid items and flow into the tracks automatically.'),
            ('repeat(3, 1fr)', '`1fr` is one share of the *free* space, so three equal columns that shrink correctly — which `33.33%` plus a gap does not.'),
            ('gap: 16px', 'Space between tracks only, never outside the grid — unlike margins, which collapse and leak.'),
        ],
        'related': (
            '`repeat(auto-fill, minmax(200px, 1fr))` for a responsive grid with no media query; '
            '`grid-template-rows`; `row-gap` / `column-gap`; `grid-auto-flow`.'
        ),
    },
    'css-media-query': {
        'summary': (
            'A media query applies its rules only when the condition holds — the basis of responsive '
            'layout without JavaScript.'
        ),
        'parts': [
            ('@media', 'An at-rule wrapping whole rules, not a property. The selectors inside keep their normal specificity.'),
            ('(max-width: 768px)', 'Inclusive: it matches at exactly 768px too. Max-width queries are "desktop first"; `min-width` is the mobile-first direction.'),
            ('display: none', 'Removes the element from layout *and* from the accessibility tree — it is hidden from screen readers too.'),
        ],
        'related': (
            'Mobile-first `min-width` queries; range syntax `(width <= 768px)`; '
            '`prefers-reduced-motion` and `prefers-color-scheme`; container queries for '
            'component-level breakpoints.'
        ),
    },
    'css-transition': {
        'summary': (
            'A transition interpolates a property between its old and new value whenever something '
            '— a class, a hover, a state change — alters it.'
        ),
        'parts': [
            ('the property', '`background-color`, not `all`: naming it avoids animating things you did not mean to and is cheaper.'),
            ('0.2s', 'The duration. Without one, the transition does nothing at all.'),
            ('ease-in-out', 'The timing function: slow at both ends. `linear` reads mechanically for colour and movement.'),
        ],
        'related': (
            '`transition-delay`; comma-separated transitions for several properties; sticking to '
            '`transform` and `opacity` for smooth animation; `@media (prefers-reduced-motion)`.'
        ),
    },
    'css-custom-property': {
        'summary': (
            'A custom property is a real CSS variable: it inherits, it can be read with `var()`, and '
            'it can be changed at runtime — including from JavaScript.'
        ),
        'parts': [
            ('--primary-color', 'The two dashes are the syntax. Names are case-sensitive.'),
            (':root', 'The `<html>` element — a selector, not a special scope. Declaring here makes it inherit everywhere.'),
            ('var(--primary-color)', 'Substitutes the value. Scoping matters: redeclare the property on any element to override it for that subtree.'),
        ],
        'related': (
            '`var(--x, fallback)`; theming by redeclaring on `[data-theme]`; '
            '`getComputedStyle(el).getPropertyValue()` and `style.setProperty()` from JS.'
        ),
    },
    'css-hover-focus': {
        'summary': (
            'Hover and focus styled together, so the affordance exists for the keyboard as well as '
            'the mouse.'
        ),
        'parts': [
            (':hover', 'Pointer over the element. It effectively does not exist on touch, which is why it must never be the only signal.'),
            (':focus', 'The element has keyboard focus. Pairing it with hover is an accessibility habit, not a stylistic one.'),
            ('the comma', 'A selector list: one rule, two selectors. An invalid selector in the list used to invalidate the whole rule — hence `:is()`.'),
        ],
        'related': (
            '`:focus-visible` to show the ring only for keyboard use; `:active`; `:hover` inside '
            '`@media (hover: hover)`; never `outline: none` without a replacement.'
        ),
    },
    'css-box-shadow': {
        'summary': (
            'A drop shadow drawn outside the box. Four values: how far across, how far down, how '
            'soft, and in what colour.'
        ),
        'parts': [
            ('0 2px', 'Horizontal then vertical offset. A little down and nothing sideways reads as light from above.'),
            ('8px', 'Blur radius. Larger and softer reads as further from the surface.'),
            ('rgba(0, 0, 0, 0.15)', 'Semi-transparent black, so the shadow tints whatever is behind it rather than painting a grey slab.'),
        ],
        'related': (
            'A fourth spread value; comma-separated layered shadows for realistic depth; `inset` for '
            'an inner shadow; `filter: drop-shadow()`, which follows the alpha of a PNG or SVG.'
        ),
    },
    'css-selectors': {
        'summary': (
            'A child combinator plus a positional pseudo-class: the *direct* children only, and of '
            'those, the one that comes first.'
        ),
        'parts': [
            ('>', 'Direct children only. A space instead would match `li`s at any depth inside `.list`.'),
            (':first-child', 'First child of its parent — and it must also match `li`, so a stray element before it means no match.'),
            ('font-weight: bold', 'Equivalent to `700`. Numbers are what variable fonts want.'),
        ],
        'related': (
            '`:first-of-type`, which ignores non-matching siblings; `:last-child` and '
            '`:nth-child()`; `+` and `~` for sibling combinators.'
        ),
    },
    'css-position-absolute': {
        'summary': (
            'Absolute positioning takes the element out of normal flow and places it against its '
            'nearest positioned ancestor.'
        ),
        'parts': [
            ('position: absolute', 'Out of flow: siblings behave as if it were not there, and it no longer stretches to fit.'),
            ('top / right', 'Offsets from the containing block\'s edges. Setting both pins the corner.'),
            ('the containing block', 'The nearest ancestor with a `position` other than `static` — usually `position: relative` on the parent. Without one it falls back to the viewport/page.'),
        ],
        'related': (
            '`position: relative` on the parent (the half everyone forgets); `inset: 0` as shorthand '
            'for all four; `z-index`; `position: fixed` and `sticky`.'
        ),
    },
    'css-grid-areas': {
        'summary': (
            'Named grid areas: the template is drawn as ASCII art, and each child is assigned to a '
            'name rather than to line numbers.'
        ),
        'parts': [
            ('grid-template-areas', 'One quoted string per row, each with one name per column. Repeating a name across rows or columns spans it.'),
            ('the strings', 'Must all have the same number of names, and each area must be rectangular. A `.` marks an empty cell.'),
            ('grid-area: sidebar', 'Places the child into that named region. Defining the names implicitly defines the grid lines too.'),
        ],
        'related': (
            '`grid-template-columns` / `rows` to size the tracks; redrawing the whole layout inside a '
            'media query, which is what makes this form worth it; `grid-row: 1 / 3` for the '
            'line-number equivalent.'
        ),
    },
    'css-flex-wrap': {
        'summary': (
            'A wrapping flex row: items sit in a line until they no longer fit, then continue on the '
            'next — the tag-list layout.'
        ),
        'parts': [
            ('flex-wrap: wrap', 'The default is `nowrap`, which squashes items instead of wrapping them. This is the fix for that.'),
            ('gap: 8px', 'Spacing between items *and* between the wrapped lines, with none at the edges.'),
        ],
        'related': (
            '`align-content` to distribute the wrapped lines; `flex: 0 0 auto` versus letting items '
            'shrink; `row-gap` / `column-gap` separately.'
        ),
    },
    'css-calc': {
        'summary': (
            'calc() computes a length from a mix of units — the only way to combine a percentage '
            'with a fixed amount.'
        ),
        'parts': [
            ('100% - 240px', 'Mixed units are the point: the percentage resolves against the parent, the pixels are absolute.'),
            ('the spaces', 'Required around `+` and `-`. `calc(100%-240px)` is invalid and silently drops the declaration.'),
        ],
        'related': (
            'Nested `calc()` and custom properties inside it; `min()`, `max()` and `clamp()`, which '
            'often express the intent better; `100dvh` for the mobile viewport problem.'
        ),
    },
    'css-clamp': {
        'summary': (
            'clamp() is a value that scales with the viewport but is bounded at both ends — fluid '
            'typography without a single media query.'
        ),
        'parts': [
            ('1.5rem', 'The minimum: it never renders smaller than this.'),
            ('4vw', 'The preferred, fluid value — 4% of viewport width, so it grows with the window.'),
            ('3rem', 'The maximum. Equivalent to `max(1.5rem, min(4vw, 3rem))`.'),
        ],
        'related': (
            '`min()` / `max()` alone; using `rem` at the bounds so browser zoom still works; '
            'clamping widths and padding the same way, not just font size.'
        ),
    },
    'css-keyframes': {
        'summary': (
            'A named keyframe sequence defines the stages of an animation; the `animation` property '
            'applies it to an element.'
        ),
        'parts': [
            ('@keyframes spin', 'Declares the name. Nothing animates until something references it.'),
            ('to { … }', 'The end state — shorthand for `100%`. The implicit start is the element\'s current value, so `from` can be omitted.'),
            ('animation: spin 1s linear infinite', 'Name, duration, timing function, iteration count, in the shorthand. `linear` matters for a spinner: `ease` makes it stutter each turn.'),
        ],
        'related': (
            '`animation-delay`, `animation-fill-mode: forwards`, `alternate` direction; '
            '`@media (prefers-reduced-motion: reduce)` to switch it off; `transform` over `top`/`left`.'
        ),
    },
    'css-transform': {
        'summary': (
            'transform moves, scales and rotates an element visually without affecting layout — '
            'which is why it is cheap enough to animate.'
        ),
        'parts': [
            ('scale(1.05)', '5% larger. It scales the whole rendered box, borders and text included, from the centre by default.'),
            ('rotate(-1deg)', 'Negative is anticlockwise.'),
            ('the single declaration', 'Functions in one `transform` compose right to left, and a second `transform` property would *replace* the first, not add to it.'),
        ],
        'related': (
            '`transform-origin`; `translate` / `scale` / `rotate` as separate modern properties; '
            '`transition: transform` to animate it; `will-change` sparingly.'
        ),
    },
    'css-before-pseudo': {
        'summary': (
            'A pseudo-element inserts generated content as the first child of the element, styleable '
            'like any box but existing only in CSS.'
        ),
        'parts': [
            ('::before', 'Two colons for pseudo-*elements*, one for pseudo-classes. It is the first child, inside the element — not before it in the document.'),
            ('content: "*"', 'Required. With no `content` the pseudo-element is not generated at all.'),
            ('margin-right: 4px', 'It is a real box, so the box model applies.'),
        ],
        'related': (
            '`::after`; `content: ""` plus a size for decorative shapes; `content: attr(data-label)`; '
            'the fact that generated content may or may not be announced by screen readers.'
        ),
    },
    'css-nth-child': {
        'summary': (
            ':nth-child() selects siblings by position with a pattern — zebra striping being the '
            'reason most people learn it.'
        ),
        'parts': [
            ('nth-child(even)', 'Keywords `even` / `odd`, or an `an+b` formula. Counting is 1-based.'),
            ('var(--color-bg-alt)', 'A themed value rather than a literal, so the stripe follows the palette.'),
            ('caveat', 'It counts *all* siblings, so a hidden or non-matching row still consumes a position.'),
        ],
        'related': (
            '`:nth-of-type()`, which counts only matching elements; `:nth-child(3n)` and '
            '`:nth-last-child()`; `tbody tr` to keep the header row out of the count.'
        ),
    },
    'css-object-fit': {
        'summary': (
            'object-fit decides how a replaced element — an image or a video — fills a box whose '
            'shape does not match its own.'
        ),
        'parts': [
            ('width / height', 'Force the box. On their own they would stretch the image out of shape.'),
            ('object-fit: cover', 'Fills the box and crops the overflow, preserving the aspect ratio. `contain` letterboxes instead of cropping.'),
        ],
        'related': (
            '`object-position` to choose which part survives the crop; `aspect-ratio` instead of a '
            'fixed height; `border-radius: 50%` for a round avatar.'
        ),
    },
    'css-aspect-ratio': {
        'summary': (
            'aspect-ratio ties an element\'s height to its width, reserving the right space before '
            'the content arrives — no padding-hack wrapper needed.'
        ),
        'parts': [
            ('16 / 9', 'Width over height. A single number works too: `1` is a square.'),
            ('width: 100%', 'One dimension still has to be determined; the ratio computes the other from it.'),
        ],
        'related': (
            '`object-fit` for the image inside; `width`/`height` attributes on an `<img>`, which give '
            'the browser the ratio for free; layout shift, which this is here to prevent.'
        ),
    },
    'css-variable-fallback': {
        'summary': (
            'var() takes a second argument used when the custom property is not defined — a default '
            'that keeps a component working outside its theme.'
        ),
        'parts': [
            ('var(--accent-color, #3b82f6)', 'Everything after the first comma is the fallback, commas and all — so it can itself be a `var()` chain.'),
            ('when it applies', 'Only when the property is genuinely undefined. A property set to an *invalid* value does not fall back here; it resolves to unset instead.'),
        ],
        'related': (
            'Nested fallbacks `var(--a, var(--b, red))`; `@property` to declare a type and a real '
            'initial value; defining the defaults on `:root` instead.'
        ),
    },
    'css-container-query': {
        'summary': (
            'A container query styles a component by the size of its *container* rather than the '
            'viewport — so the same card can adapt in a sidebar and in a full-width page.'
        ),
        'parts': [
            ('container-type: inline-size', 'Opts the wrapper in, and is required. It makes the element a query container along the inline axis.'),
            ('@container (min-width: 400px)', 'Matches against the nearest ancestor container, not the window.'),
            ('the rules inside', 'Apply to the descendants of the container — a container cannot query and then style itself.'),
        ],
        'related': (
            '`container-name` and `@container name (…)`; container query units `cqw` / `cqi`; media '
            'queries for genuinely page-level decisions.'
        ),
    },
    'css-backdrop-filter': {
        'summary': (
            'backdrop-filter applies a graphical filter to whatever is *behind* the element — the '
            'frosted-glass overlay effect.'
        ),
        'parts': [
            ('blur(8px)', 'The filter function, applied to the backdrop rather than to the element\'s own content (that would be `filter`).'),
            ('background: rgba(0,0,0,0.3)', 'Needed alongside it: the element must be semi-transparent for there to be a visible backdrop, and the tint keeps text readable.'),
        ],
        'related': (
            'Other functions — `saturate()`, `brightness()` — combined in one value; `filter` for the '
            'element itself; the real rendering cost, especially on mobile.'
        ),
    },
    'css-sticky': {
        'summary': (
            'Sticky positioning is relative until the element reaches a given offset in its scroll '
            'container, then fixed — a header that pins itself while its section scrolls.'
        ),
        'parts': [
            ('position: sticky', 'Stays in flow, so nothing jumps when it sticks. It sticks within its *parent*, and scrolls away with it.'),
            ('top: 0', 'The threshold. Without at least one offset it never sticks at all.'),
            ('z-index: 10', 'Puts it above the content it now overlaps.'),
        ],
        'related': (
            'An ancestor with `overflow: hidden` or `auto`, which silently breaks it; '
            '`scroll-margin-top` so anchors are not hidden underneath; `position: fixed` for '
            'viewport-pinned elements.'
        ),
    },
    'css-text-ellipsis': {
        'summary': (
            'Single-line truncation with an ellipsis. All three declarations are required — each one '
            'alone does nothing useful.'
        ),
        'parts': [
            ('white-space: nowrap', 'Stops the text wrapping, so it overflows in one line.'),
            ('overflow: hidden', 'Clips the overflow. Without it the text simply spills out.'),
            ('text-overflow: ellipsis', 'Draws the … in place of the clipped part. It only applies to overflow that is actually hidden.'),
        ],
        'related': (
            'A width or `min-width: 0` on a flex item, or nothing overflows; `-webkit-line-clamp` '
            'for multi-line truncation; `title` so the full text is still reachable.'
        ),
    },
    'css-attribute-selector': {
        'summary': (
            'An attribute selector matches on markup rather than on classes, here combined with a '
            'validity pseudo-class to style a field by its own state.'
        ),
        'parts': [
            ('input[type="email"]', 'Exact attribute match. Other forms: `^=` starts with, `$=` ends with, `*=` contains.'),
            (':invalid', 'Applies while the value fails the field\'s own constraints — and, awkwardly, while it is still empty and `required`.'),
            ('specificity', 'An attribute selector weighs the same as a class, so this rule beats a plain `input` rule.'),
        ],
        'related': (
            '`:user-invalid`, which waits until the field has been interacted with; `:valid`, '
            '`:required`, `:placeholder-shown`; `[data-*]` selectors for your own state.'
        ),
    },
    # --- DOM / Browser APIs ---
    'dom-query-selector': {
        'summary': (
            'The two CSS-selector lookups: one element, or all of them. Any selector that works in a '
            'stylesheet works here.'
        ),
        'parts': [
            ('querySelector(sel)', 'The *first* match in document order, or `null` — which is why the result is usually guarded before use.'),
            ('querySelectorAll(sel)', 'A static NodeList of every match. It does not update as the DOM changes, and it is empty rather than null when nothing matches.'),
            ('the NodeList', 'Has `forEach` and is iterable, but not `map` or `filter` — spread it or use `Array.from` for those.'),
        ],
        'related': (
            '`getElementById`, which is faster and needs no `#`; `el.querySelector` to scope the '
            'search to a subtree; `closest()` for walking upwards; `matches()` for a test.'
        ),
    },
    'dom-add-event-listener': {
        'summary': (
            'addEventListener attaches a handler for a named event. Unlike an `onclick` property it '
            'stacks — several handlers can listen to the same event on the same element.'
        ),
        'parts': [
            ("'click'", 'The event type, lower-case and without an "on" prefix. A typo fails silently.'),
            ('the handler', 'Called with the event object. `event.target` is what was actually clicked; `event.currentTarget` is the element the listener sits on.'),
        ],
        'related': (
            '`removeEventListener` with the same function reference; the options object — `once`, '
            '`passive`, `capture`, `signal`; delegation from a parent for dynamic children.'
        ),
    },
    'dom-classlist': {
        'summary': (
            'classList edits individual classes without touching the rest of the attribute — which '
            'writing to `className` would clobber.'
        ),
        'parts': [
            ('.add(...)', 'Adds; a class already present is a no-op. It takes several names at once.'),
            ('.remove(...)', 'Removes; a class that is absent is not an error.'),
            ('.toggle(name)', 'Flips it, and returns whether it is now present. A second boolean argument forces the direction — `toggle(\'open\', isOpen)`.'),
        ],
        'related': (
            '`.contains(name)` to test; `.replace(a, b)`; `data-*` attributes plus attribute '
            'selectors when the state is really a value rather than a flag.'
        ),
    },
    'dom-create-element': {
        'summary': (
            'Building a node in three steps: create it detached, fill it in, then attach it — the '
            'order that avoids a reflow per edit.'
        ),
        'parts': [
            ('createElement(tag)', 'Returns a detached element. It is not in the document, and therefore not rendered, until appended.'),
            ('textContent', 'Sets text safely. `innerHTML` with untrusted input is how markup injection happens.'),
            ('appendChild(node)', 'Attaches it as the last child. It *moves* the node if it was already elsewhere in the document.'),
        ],
        'related': (
            '`append()`, which takes several nodes and plain strings; `DocumentFragment` for batching '
            'many inserts; `insertBefore` / `prepend`; `remove()`.'
        ),
    },
    'dom-dataset': {
        'summary': (
            'dataset exposes every `data-*` attribute on an element as a property — the standard way '
            'to read state that markup carried down to a handler.'
        ),
        'parts': [
            ('data-user-id → dataset.userId', 'Hyphens become camelCase. This mapping is the part that trips people up.'),
            ('the value', 'Always a string, or `undefined` when the attribute is absent. `Number(...)` it before doing arithmetic.'),
            ('event.target', 'The element the event started on — which in a delegated handler may be a child of the one holding the attribute.'),
        ],
        'related': (
            '`closest(\'[data-user-id]\')` to find the element that actually carries it; '
            '`getAttribute`/`setAttribute`; attribute selectors for styling from the same data.'
        ),
    },
    'dom-fetch-post': {
        'summary': (
            'A POST with a JSON body: method, headers and a serialised body in fetch\'s options '
            'object. None of the three is optional for a JSON API.'
        ),
        'parts': [
            ("method: 'POST'", 'Defaults to GET, which is why a forgotten method silently sends the wrong request.'),
            ("headers: { 'Content-Type': 'application/json' }", 'Tells the server how to parse the body. Without it many frameworks hand you an empty payload.'),
            ('body: JSON.stringify({ name })', 'The body must be a string (or FormData/Blob) — passing the object itself sends "[object Object]".'),
        ],
        'related': (
            '`res.ok` and `res.status`, since fetch does not reject on a 4xx; `await res.json()`; '
            '`credentials: \'include\'` for cookies; `signal` for cancellation.'
        ),
    },
    'dom-local-storage': {
        'summary': (
            'localStorage is a synchronous string-to-string store that survives tab and browser '
            'restarts, scoped to the origin.'
        ),
        'parts': [
            ('setItem(key, value)', 'Both are coerced to strings. Objects need `JSON.stringify` first.'),
            ('getItem(key)', 'Returns `null` — not undefined — for a key that was never set, which is what the `??` here is catching.'),
            ('synchronous', 'It blocks the main thread, so it is for small values, not for bulk data.'),
        ],
        'related': (
            '`removeItem` / `clear`; the `storage` event for cross-tab sync; the quota, which throws '
            'when exceeded; IndexedDB for anything sizeable.'
        ),
    },
    'dom-session-storage': {
        'summary': (
            'sessionStorage has the same API as localStorage but its contents die with the tab — the '
            'right home for a draft or a step in a flow.'
        ),
        'parts': [
            ('scope', 'Per tab *and* per origin. A duplicated tab starts with a copy; a new tab starts empty.'),
            ('removeItem(key)', 'Deletes one entry. Absent keys are silently ignored.'),
        ],
        'related': (
            '`localStorage` when it must outlive the tab; `clear()`; the same '
            'stringify/parse round-trip for structured values.'
        ),
    },
    'dom-set-timeout': {
        'summary': (
            'setTimeout schedules a callback once, after at least the given delay, and returns a '
            'handle that can cancel it.'
        ),
        'parts': [
            ('the delay', 'A *minimum*, not a guarantee: the callback waits for the current task to finish, and background tabs are throttled.'),
            ('the return value', 'The timer id. Keeping it is the only way to cancel later.'),
            ('clearTimeout(timer)', 'Cancels a pending callback. Harmless on a timer that has already fired.'),
        ],
        'related': (
            'Extra arguments passed through to the callback; clearing on unmount so it cannot fire '
            'into a dead component; `setInterval` for repetition; `queueMicrotask` for "as soon as '
            'possible".'
        ),
    },
    'dom-set-interval': {
        'summary': (
            'setInterval runs a callback repeatedly at a fixed period until it is cleared. Nothing '
            'stops it on its own — an uncleared interval is a classic leak.'
        ),
        'parts': [
            ('the period', 'Time between *starts*. If the callback takes longer than the period, calls pile up rather than overlap.'),
            ('clearInterval(interval)', 'The only way it ends. Store the handle for it.'),
        ],
        'related': (
            'A self-rescheduling `setTimeout` when the work is of variable length; '
            '`requestAnimationFrame` for anything visual; clearing it in an effect cleanup.'
        ),
    },
    'dom-form-data': {
        'summary': (
            'FormData collects a form\'s current values straight from the DOM, keyed by each '
            'control\'s `name` — no per-field reads.'
        ),
        'parts': [
            ('new FormData(formEl)', 'Reads every named, enabled control. Unchecked checkboxes and unnamed fields simply do not appear.'),
            ('.get(name)', 'One value, or `null` when absent. `getAll` for a multi-value field.'),
            ('as a fetch body', 'Passing it straight to fetch sends `multipart/form-data` with the boundary set for you — so do *not* set the content-type yourself.'),
        ],
        'related': (
            '`Object.fromEntries(formData)` for a plain object; `.append` / `.set` for extra fields; '
            'file inputs, which this handles and JSON does not.'
        ),
    },
    'dom-prevent-default': {
        'summary': (
            'Two different cancellations: one stops the browser\'s own reaction to the event, the '
            'other stops the event travelling further up the tree.'
        ),
        'parts': [
            ('preventDefault()', 'Cancels the default action — here the full page reload a form submit performs. Propagation continues.'),
            ('stopPropagation()', 'Stops ancestors from seeing the event. The default action still happens unless also prevented.'),
            ('order', 'Call them before any `await`: once the handler yields, the chance to cancel has passed.'),
        ],
        'related': (
            '`stopImmediatePropagation()` to block sibling listeners too; `event.defaultPrevented`; '
            'passive listeners, where `preventDefault` is ignored; the capture phase.'
        ),
    },
    'dom-intersection-observer': {
        'summary': (
            'IntersectionObserver reports when an element enters or leaves the viewport, '
            'asynchronously and without a scroll handler — the infinite-scroll and lazy-load tool.'
        ),
        'parts': [
            ('the callback', 'Receives an array of entries, since one observer can watch many elements and several can cross at once.'),
            ('entry.isIntersecting', 'True on the way in, false on the way out — so the check is what stops `loadMore()` firing when it scrolls back off.'),
            ('observer.observe(el)', 'Starts watching. Nothing happens until this is called, and the callback fires once immediately with the initial state.'),
        ],
        'related': (
            'The options object — `root`, `rootMargin` to prefetch early, `threshold`; `unobserve` '
            'and `disconnect` for cleanup; `entry.intersectionRatio`.'
        ),
    },
    'dom-mutation-observer': {
        'summary': (
            'MutationObserver reports DOM changes you did not make — for reacting to markup written '
            'by code outside your control.'
        ),
        'parts': [
            ('the callback', 'Batched: it receives a list of mutation records, delivered as a microtask rather than per change.'),
            ('childList: true', 'Watch for children added or removed. At least one of childList, attributes or characterData is required.'),
            ('subtree: true', 'Extends the watch to every descendant, not just direct children.'),
        ],
        'related': (
            '`attributes` with `attributeFilter`; `disconnect()` and `takeRecords()`; the risk of '
            'observing changes your own callback causes; ResizeObserver for size instead of structure.'
        ),
    },
    'dom-match-media': {
        'summary': (
            'matchMedia evaluates a media query from JavaScript and can notify you when its result '
            'flips — the same conditions CSS uses, available to code.'
        ),
        'parts': [
            ('matchMedia(query)', 'Returns a MediaQueryList. `.matches` is the current boolean answer, readable immediately.'),
            ("addEventListener('change', …)", 'Fires when the answer changes — here, when the OS theme is switched while the page is open.'),
            ('e.matches', 'The new value, off the event, so no re-read of the list is needed.'),
        ],
        'related': (
            '`prefers-reduced-motion` and `(min-width: …)` through the same API; '
            '`removeEventListener` on cleanup; the older `addListener`, deprecated.'
        ),
    },
    'dom-clipboard': {
        'summary': (
            'The async Clipboard API writes text to the system clipboard. It returns a promise and '
            'is only permitted from a user gesture, in a secure context.'
        ),
        'parts': [
            ('navigator.clipboard.writeText(text)', 'Resolves when the write succeeds; rejects when permission is denied — so it belongs in a try/catch.'),
            ('await', 'Genuinely asynchronous, and the call must originate in a click or key handler, not on a timer.'),
        ],
        'related': (
            '`readText()`, which prompts for permission; `write()` with a ClipboardItem for images; '
            'the deprecated `document.execCommand(\'copy\')`; confirming the copy in the UI.'
        ),
    },
    'dom-history-pushstate': {
        'summary': (
            'pushState adds a history entry and changes the URL without a navigation — the mechanism '
            'every client-side router is built on.'
        ),
        'parts': [
            ('the state object', 'Stored with the entry and handed back on `popstate`. It must be structured-cloneable, so no functions or DOM nodes.'),
            ('the title argument', "Ignored by browsers. It is passed as '' purely because the signature requires it."),
            ('the URL', 'Must be same-origin. It changes the address bar and nothing else: no request is made and no re-render happens.'),
        ],
        'related': (
            'The `popstate` event for back and forward; `replaceState` when the current entry should '
            'not be kept; rendering yourself, since pushState does not.'
        ),
    },
    'dom-request-animation-frame': {
        'summary': (
            'requestAnimationFrame schedules a callback for just before the next repaint, so an '
            'animation runs at the display\'s own rate and pauses in a background tab.'
        ),
        'parts': [
            ('the recursive call', 'One frame is scheduled at a time; re-scheduling inside the callback is what makes it a loop.'),
            ('the kick-off', 'The call outside the function starts the first frame. Without it, nothing runs.'),
            ('the timestamp argument', 'The callback receives a high-resolution time — use it to make movement time-based rather than frame-count-based.'),
        ],
        'related': (
            '`cancelAnimationFrame(id)` to stop the loop; `setInterval`, which drifts and does not '
            'align to paint; CSS transitions and animations when they suffice.'
        ),
    },
    'dom-custom-event': {
        'summary': (
            'A CustomEvent is your own event type, dispatched on a real DOM target so it bubbles and '
            'can be listened for exactly like a click.'
        ),
        'parts': [
            ('new CustomEvent(type, options)', 'The name is yours. A hyphenated name keeps it clear of built-in types.'),
            ('detail', 'The one place a payload may go. Listeners read it as `event.detail`.'),
            ('dispatchEvent(event)', 'Fires it synchronously — every listener has run by the time the call returns.'),
        ],
        'related': (
            '`bubbles: true` and `composed: true`, both false by default — a non-bubbling event only '
            'reaches listeners on the target itself; `cancelable` plus `preventDefault`.'
        ),
    },
    'dom-closest': {
        'summary': (
            'closest walks up from an element — itself included — and returns the first ancestor '
            'matching a selector. The backbone of event delegation.'
        ),
        'parts': [
            ('the selector', 'Any CSS selector. Testing the element itself first is the behaviour that makes delegation work whether the click landed on the row or on its icon.'),
            ('the return', '`null` when nothing up the tree matches, so guard it before use.'),
        ],
        'related': (
            '`matches()` when only the element itself matters; `dataset` on the found ancestor to '
            'get its id; `parentElement` for a single step up.'
        ),
    },
}
