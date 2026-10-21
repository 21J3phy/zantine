// `npm test`: scripts/ignore-build.sh against Git histories built here.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = fileURLToPath(new URL('./ignore-build.sh', import.meta.url));
const SKIP = 0;
const BUILD = 1;

const ENV = {
  ...process.env,
  GIT_CONFIG_GLOBAL: '/dev/null',
  GIT_CONFIG_NOSYSTEM: '1',
  GIT_AUTHOR_NAME: 'test',
  GIT_AUTHOR_EMAIL: 'test@example.com',
  GIT_COMMITTER_NAME: 'test',
  GIT_COMMITTER_EMAIL: 'test@example.com',
};
delete ENV.VERCEL_GIT_PREVIOUS_SHA;

/** A repository shaped like this one: the site in web/, everything else beside it. */
function repo(t) {
  const root = mkdtempSync(join(tmpdir(), 'ignore-build-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, { cwd: root, env: ENV, encoding: 'utf8' }).trim();
  git('init', '-q', '-b', 'main');
  /** Commit `text` to `path`; resolves to the new commit's SHA. */
  const commit = (path, text) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), text);
    git('add', '-A');
    git('commit', '-q', '-m', `change ${path}`);
    return git('rev-parse', 'HEAD');
  };
  return { root, git, commit };
}

/** The script's exit code in `checkout`/web, given Vercel's last successful deployment SHA. */
function decide(checkout, previous) {
  const env = previous === undefined ? ENV : { ...ENV, VERCEL_GIT_PREVIOUS_SHA: previous };
  return spawnSync('sh', [SCRIPT], { cwd: join(checkout, 'web'), env, encoding: 'utf8' }).status;
}

test("builds a branch's first deployment", (t) => {
  const { root, commit } = repo(t);
  commit('web/index.html', 'v1');
  assert.equal(decide(root, undefined), BUILD);
  assert.equal(decide(root, ''), BUILD);
});

test('skips when web/ is unchanged since the last successful deployment', (t) => {
  const { root, commit } = repo(t);
  const deployed = commit('web/index.html', 'v1');
  commit('engine/rules.py', 'v1');
  commit('engine/rules.py', 'v2');
  assert.equal(decide(root, deployed), SKIP);
});

test('builds a web change whose deployment failed once an engine-only commit follows', (t) => {
  const { root, commit } = repo(t);
  const deployed = commit('web/index.html', 'v1');
  commit('web/index.html', 'v2'); // its deployment failed, so Vercel still reports `deployed`
  commit('engine/rules.py', 'v1');
  assert.equal(decide(root, deployed), BUILD);
});

test('builds a redeploy of the deployed commit', (t) => {
  const { root, commit } = repo(t);
  const deployed = commit('web/index.html', 'v1');
  assert.equal(decide(root, deployed), BUILD);
});

test('builds when the last deployed commit is not in the clone', (t) => {
  const { root, commit } = repo(t);
  const deployed = commit('web/index.html', 'v1');
  commit('engine/rules.py', 'v1');
  commit('engine/rules.py', 'v2');
  const shallow = join(root, '..', `${root.split('/').pop()}-shallow`);
  t.after(() => rmSync(shallow, { recursive: true, force: true }));
  execFileSync('git', ['clone', '-q', '--depth', '1', `file://${root}`, shallow], { env: ENV });
  assert.equal(decide(shallow, deployed), BUILD);
  assert.equal(decide(root, '0'.repeat(40)), BUILD);
});
