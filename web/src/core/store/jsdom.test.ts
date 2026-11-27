/** The store under the project's default test environment (jsdom), as the UI tests will use it. */
import { expect, it } from 'vitest';
import { MemoryFileStore, SqliteDriveStore, newDrive, column } from './index';

it('opens, writes and lists a drive under jsdom', async () => {
  const store = await SqliteDriveStore.open(new MemoryFileStore());
  newDrive(store.hdb, { started_unix: 1794812400 }, (w) => {
    w.addStream('s', [column('rpm')]);
    w.append('s', 0, [800]);
  });
  const list = await store.listDrives();
  expect(list).toHaveLength(1);
  const loaded = await store.loadDrive(list[0]!.name);
  expect(Array.from(loaded.series.rpm!.v)).toEqual([800]);
  await store.close();
});
