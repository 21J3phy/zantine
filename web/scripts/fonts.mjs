// Before `vite build` and `vite`: make sure public/fonts/zodiak/ holds an intact copy of Zodiak.
//
// Zodiak's license (ITF Free Font License) lets us serve it on our own site but not share the
// files through a repository, so they are not in git. This downloads the official package from
// Fontshare and copies out, unmodified, the two variable WOFF2 files the site uses and the license.
//
// Files already there are checked first, and a missing or damaged one (say, cut short by an
// interrupted run) means a fresh download. A download is checked in full before anything is
// replaced, and each file is moved into place whole, so a failed run leaves the folder as it was.
//
// Offline, a local run warns and carries on (headings fall back to Iowan Old Style or Georgia).
// On Vercel (VERCEL is set) a failure stops the build, so a deploy never ships without the font.

import { inflateRawSync } from 'node:zlib';
import { realpathSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = 'https://api.fontshare.com/v2/fonts/download/zodiak';
/** Path in the Fontshare package → file name here. */
const FILES = {
  'Zodiak_Complete/Fonts/WEB/fonts/Zodiak-Variable.woff2': 'Zodiak-Variable.woff2',
  'Zodiak_Complete/Fonts/WEB/fonts/Zodiak-VariableItalic.woff2': 'Zodiak-VariableItalic.woff2',
  'Zodiak_Complete/License/FFL.txt': 'LICENSE-FFL.txt',
};

/** The entries of a ZIP archive, by name, read from its central directory. */
function unzip(buf) {
  const end = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (end < 0) throw new Error('not a ZIP archive');
  const files = new Map();
  let p = buf.readUInt32LE(end + 16);
  for (let i = buf.readUInt16LE(end + 10); i > 0; i--) {
    const method = buf.readUInt16LE(p + 10);
    const size = buf.readUInt32LE(p + 20);
    const nameLen = buf.readUInt16LE(p + 28);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);
    const local = buf.readUInt32LE(p + 42);
    const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
    const data = buf.subarray(start, start + size);
    files.set(name, () => (method === 8 ? inflateRawSync(data) : data));
    p += 46 + nameLen + buf.readUInt16LE(p + 30) + buf.readUInt16LE(p + 32);
  }
  return files;
}

/** What is wrong with `data` as the file `name`, or '' if nothing. */
function fault(name, data) {
  if (name.endsWith('.woff2')) {
    if (data.length < 48 || data.toString('latin1', 0, 4) !== 'wOF2') return 'not a WOFF2 file';
    // The WOFF2 header records the file's full length, so a copy cut short shows up here.
    return data.readUInt32BE(8) === data.length ? '' : 'truncated';
  }
  return data.includes('ITF Free Font License') ? '' : 'not the ITF Free Font License';
}

/**
 * Make `dir` hold the three files, intact, downloading the package from `source` when one is
 * missing or damaged. The download is unpacked in a new folder under `stage`, which must be on
 * the same disk as `dir`. Resolves to whether it downloaded.
 */
export async function ensureFonts({ dir, stage, source = SOURCE, get = fetch }) {
  const intact = (to) => readFile(join(dir, to)).then((data) => !fault(to, data), () => false);
  if ((await Promise.all(Object.values(FILES).map(intact))).every(Boolean)) return false;

  const res = await get(source);
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${source}`);
  const zip = unzip(Buffer.from(await res.arrayBuffer()));
  const files = Object.entries(FILES).map(([from, to]) => {
    const read = zip.get(from);
    if (!read) throw new Error(`${from} is missing from the Fontshare package`);
    const data = read();
    const why = fault(to, data);
    if (why) throw new Error(`${from} in the Fontshare package is ${why}`);
    return [to, data];
  });

  await mkdir(stage, { recursive: true });
  const tmp = await mkdtemp(join(stage, 'zodiak-'));
  try {
    for (const [to, data] of files) await writeFile(join(tmp, to), data);
    await mkdir(dir, { recursive: true });
    for (const [to] of files) await rename(join(tmp, to), join(dir, to));
  } finally {
    await rm(tmp, { recursive: true, force: true });
  }
  return true;
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  try {
    const dir = join(ROOT, 'public', 'fonts', 'zodiak');
    if (await ensureFonts({ dir, stage: join(ROOT, 'node_modules', '.cache') }))
      console.log('fonts: Zodiak downloaded from Fontshare into public/fonts/zodiak/');
  } catch (err) {
    if (process.env.VERCEL) throw err;
    console.warn(`fonts: could not download Zodiak (${err.message}); headings will use the fallback serif.`);
  }
}
