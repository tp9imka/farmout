import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const STATIC = fileURLToPath(new URL('../../static/', import.meta.url));
const BUNDLE_SHA256 = '72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7';
const LICENCE = /(LICENSE|OFL\.txt)$/;
const TEXT = /\.(html|js|mjs|css)$/;
// Preact's SVG namespace is an identifier, never fetched.
const ALLOWED_URLS = new Set(['http://www.w3.org/2000/svg']);

const walk = dir => readdirSync(dir).flatMap(n => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? walk(p) : [p];
});

test('vendored htm+preact bundle matches the pinned sha256', () => {
  const sha = createHash('sha256').update(readFileSync(join(STATIC, 'vendor/htm-preact-standalone.mjs'))).digest('hex');
  assert.equal(sha, BUNDLE_SHA256);
});

test('no network URLs in static/ outside licence files', () => {
  for (const f of walk(STATIC).filter(p => TEXT.test(p) && !LICENCE.test(p))) {
    const urls = readFileSync(f, 'utf8').match(/https?:\/\/[^\s"'`)]+/g) || [];
    assert.deepEqual(urls.filter(u => !ALLOWED_URLS.has(u)), [], f);
  }
});

test('every /static/ asset referenced by index.html and styles.css exists', () => {
  const html = readFileSync(join(STATIC, 'index.html'), 'utf8');
  const refs = [...html.matchAll(/(?:href|src)="\/static\/([^"]+)"/g)].map(m => m[1]);
  assert.ok(refs.includes('app.js') && refs.includes('styles.css'));
  const css = readFileSync(join(STATIC, 'styles.css'), 'utf8');
  refs.push(...[...css.matchAll(/url\("([^"]+)"\)/g)].map(m => m[1]));
  for (const r of refs) assert.ok(existsSync(join(STATIC, r)), r);
});
