// Before `vite build` and `vite`: put Zodiak in public/fonts/zodiak/ if it is not there yet.
//
// Zodiak's license (ITF Free Font License) lets us serve it on our own site but not share the
// files through a repository, so they are not in git. This downloads the official package from
// Fontshare and copies out, unmodified, the two variable WOFF2 files the site uses and the license.
//
// Offline, a local run warns and carries on (headings fall back to Iowan Old Style or Georgia).
// On Vercel (VERCEL is set) a failure stops the build, so a deploy never ships without the font.

import { inflateRawSync } from 'node:zlib';
import { access, mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = join(resolve(dirname(fileURLToPath(import.meta.url)), '..'), 'public', 'fonts', 'zodiak');
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

const have = await Promise.all(Object.values(FILES).map((f) => access(join(DIR, f)).then(() => true, () => false)));
if (!have.every(Boolean)) {
  try {
    const res = await fetch(SOURCE);
    if (!res.ok) throw new Error(`HTTP ${res.status} from ${SOURCE}`);
    const zip = unzip(Buffer.from(await res.arrayBuffer()));
    await mkdir(DIR, { recursive: true });
    for (const [from, to] of Object.entries(FILES)) {
      const read = zip.get(from);
      if (!read) throw new Error(`${from} is missing from the Fontshare package`);
      const data = read();
      if (to.endsWith('.woff2') && data.toString('latin1', 0, 4) !== 'wOF2') throw new Error(`${from} is not a WOFF2 file`);
      await writeFile(join(DIR, to), data);
    }
    console.log(`fonts: Zodiak downloaded from Fontshare into public/fonts/zodiak/`);
  } catch (err) {
    if (process.env.VERCEL) throw err;
    console.warn(`fonts: could not download Zodiak (${err.message}); headings will use the fallback serif.`);
  }
}
