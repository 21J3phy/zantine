// `npm test`: scripts/fonts.mjs against packages built here, with no network.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { crc32, deflateRawSync } from 'node:zlib';
import { ensureFonts } from './fonts.mjs';

const WEB = 'Zodiak_Complete/Fonts/WEB/fonts';
const LICENSE_PATH = 'Zodiak_Complete/License/FFL.txt';

/** Random bytes behind a WOFF2 header that records `size` as the file's length. */
function font(size) {
  const data = randomBytes(size);
  data.write('wOF2', 0, 'latin1');
  data.writeUInt32BE(size, 8);
  return data;
}

const FONT = font(4000);
const ITALIC = font(5000);
const LICENSE = Buffer.from('ITF Free Font License (FFL)\nVersion 2.0\n');

/** A ZIP archive of `[name, data, method]` entries, method 0 (stored) or 8 (deflated). */
function zip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (const [name, data, method] of entries) {
    const body = method === 8 ? deflateRawSync(data) : data;
    const path = Buffer.from(name);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(crc32(data), 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(path.length, 26);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(method, 10);
    entry.writeUInt32LE(crc32(data), 16);
    entry.writeUInt32LE(body.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(path.length, 28);
    entry.writeUInt32LE(offset, 42);
    parts.push(local, path, body);
    central.push(entry, path);
    offset += 30 + path.length + body.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

const PACKAGE = zip([
  [`${WEB}/Zodiak-Variable.woff2`, FONT, 0],
  [`${WEB}/Zodiak-VariableItalic.woff2`, ITALIC, 8],
  [LICENSE_PATH, LICENSE, 8],
]);

/** A stand-in for `fetch` that answers every request with `body`, counting the requests. */
function serve(body, status = 200) {
  const get = async () => {
    get.calls++;
    return { ok: status < 300, status, arrayBuffer: async () => new Uint8Array(body).buffer };
  };
  get.calls = 0;
  return get;
}

const offline = async () => {
  throw new TypeError('fetch failed');
};

/** A fresh project folder; `fonts` mirrors public/fonts/zodiak/ and `stage` node_modules/.cache. */
async function project(t) {
  const root = await mkdtemp(join(tmpdir(), 'fonts-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, dir: join(root, 'public', 'fonts', 'zodiak'), stage: join(root, 'cache') };
}

async function contents(dir) {
  const names = (await readdir(dir)).sort();
  return Object.fromEntries(await Promise.all(names.map(async (n) => [n, await readFile(join(dir, n))])));
}

const INTACT = { 'LICENSE-FFL.txt': LICENSE, 'Zodiak-Variable.woff2': FONT, 'Zodiak-VariableItalic.woff2': ITALIC };

test('copies stored and deflated files out unmodified and cleans up after itself', async (t) => {
  const { dir, stage } = await project(t);
  const get = serve(PACKAGE);
  assert.equal(await ensureFonts({ dir, stage, get }), true);
  assert.deepEqual(await contents(dir), INTACT);
  assert.deepEqual(await readdir(stage), []);
  assert.equal(get.calls, 1);
});

test('keeps an intact folder without downloading', async (t) => {
  const { dir, stage } = await project(t);
  await ensureFonts({ dir, stage, get: serve(PACKAGE) });
  assert.equal(await ensureFonts({ dir, stage, get: offline }), false);
  assert.deepEqual(await contents(dir), INTACT);
});

test('repairs a font an interrupted run left empty', async (t) => {
  const { dir, stage } = await project(t);
  await ensureFonts({ dir, stage, get: serve(PACKAGE) });
  await writeFile(join(dir, 'Zodiak-VariableItalic.woff2'), '');
  const get = serve(PACKAGE);
  assert.equal(await ensureFonts({ dir, stage, get }), true);
  assert.equal(get.calls, 1);
  assert.deepEqual(await contents(dir), INTACT);
});

test('repairs a font cut short', async (t) => {
  const { dir, stage } = await project(t);
  await ensureFonts({ dir, stage, get: serve(PACKAGE) });
  await writeFile(join(dir, 'Zodiak-Variable.woff2'), FONT.subarray(0, 2000));
  assert.equal(await ensureFonts({ dir, stage, get: serve(PACKAGE) }), true);
  assert.deepEqual(await contents(dir), INTACT);
});

test('fails, rather than passing a damaged folder, when it cannot download', async (t) => {
  const { dir, stage } = await project(t);
  await ensureFonts({ dir, stage, get: serve(PACKAGE) });
  await writeFile(join(dir, 'Zodiak-VariableItalic.woff2'), '');
  await assert.rejects(ensureFonts({ dir, stage, get: offline }), /fetch failed/);
  await assert.rejects(ensureFonts({ dir, stage, get: serve(Buffer.alloc(0), 503) }), /HTTP 503/);
});

test('leaves the folder as it was when the package is bad', async (t) => {
  const { dir, stage } = await project(t);
  // An earlier run that stopped part way: only the license made it.
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'LICENSE-FFL.txt'), LICENSE);
  const bad = {
    'missing an entry': zip([
      [`${WEB}/Zodiak-Variable.woff2`, FONT, 0],
      [`${WEB}/Zodiak-VariableItalic.woff2`, ITALIC, 0],
    ]),
    'holding a file that is not WOFF2': zip([
      [`${WEB}/Zodiak-Variable.woff2`, FONT, 0],
      [`${WEB}/Zodiak-VariableItalic.woff2`, Buffer.from('<html>not a font</html>'), 0],
      [LICENSE_PATH, LICENSE, 0],
    ]),
    'holding a truncated font': zip([
      [`${WEB}/Zodiak-Variable.woff2`, FONT, 0],
      [`${WEB}/Zodiak-VariableItalic.woff2`, ITALIC.subarray(0, 100), 8],
      [LICENSE_PATH, LICENSE, 0],
    ]),
    'not a ZIP archive': Buffer.from('<html>maintenance</html>'),
  };
  for (const [what, body] of Object.entries(bad)) {
    await assert.rejects(ensureFonts({ dir, stage, get: serve(body) }), Error, what);
    assert.deepEqual(await contents(dir), { 'LICENSE-FFL.txt': LICENSE }, what);
  }
});

test('leaves the folder as it was when writing the download fails', async (t) => {
  const { root, dir } = await project(t);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'LICENSE-FFL.txt'), LICENSE);
  const stage = join(root, 'not-a-folder');
  await writeFile(stage, '');
  await assert.rejects(ensureFonts({ dir, stage, get: serve(PACKAGE) }));
  assert.deepEqual(await contents(dir), { 'LICENSE-FFL.txt': LICENSE });
});
