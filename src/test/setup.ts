import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// With `globals: false` Testing Library never registers its auto-cleanup, so
// rendered trees (and their window listeners) would leak into the next test.
afterEach(cleanup);
