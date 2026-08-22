import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.hoisted(() => {
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

const idb = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => void idb.set(k, v),
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const { storePageSave, getPageSave, listPageSaves, clearPageSave } =
  await import('./pageStore');

const png = () => new Blob(['\x89PNG'], { type: 'image/png' });
const payload = (strokes: string, revision = 1) => ({
  strokes,
  width: 2100,
  height: 2970,
  revision,
});

beforeEach(() => idb.clear());

describe('pageStore', () => {
  it('keeps one record per page, always the newest', async () => {
    // A page save is a PUT of the whole page, so ten queued saves of one page
    // are nine uploads of something nobody will ever see. An afternoon of
    // writing with no signal has to cost one upload, carrying the afternoon.
    await storePageSave('page1', payload('[1]', 1), png());
    await storePageSave('page1', payload('[1,2]', 2), png());
    await storePageSave('page1', payload('[1,2,3]', 3), png());

    const saves = await listPageSaves();
    expect(saves).toHaveLength(1);
    expect((await getPageSave('page1'))!.meta.strokes).toBe('[1,2,3]');
    expect((await getPageSave('page1'))!.meta.revision).toBe(3);
  });

  it('keeps pages apart', async () => {
    await storePageSave('page1', payload('[1]'), png());
    await storePageSave('page2', payload('[2]'), png());
    expect((await listPageSaves()).map(s => s.pageId).sort()).toEqual([
      'page1',
      'page2',
    ]);
  });

  it('will not drop ink drawn while the upload was in flight', async () => {
    // clearPageSave runs after the server confirms. If more was written in
    // between, that record is newer than what was uploaded and deleting it
    // would throw away strokes the server has never seen.
    const first = await storePageSave('page1', payload('[1]', 1), png());
    await storePageSave('page1', payload('[1,2]', 2), png());

    await clearPageSave('page1', first.revision);

    expect((await getPageSave('page1'))!.meta.strokes).toBe('[1,2]');
  });

  it('drops the record it actually uploaded', async () => {
    const only = await storePageSave('page1', payload('[1]'), png());
    await clearPageSave('page1', only.revision);
    expect(await getPageSave('page1')).toBeUndefined();
    expect(idb.size).toBe(0);
  });
});
