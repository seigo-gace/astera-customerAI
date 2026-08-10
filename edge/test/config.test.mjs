import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const configUrl = new URL('../wrangler.toml', import.meta.url);

async function config() {
  return readFile(configUrl, 'utf8');
}

test('Cloudflare route is limited to the Customer AI API namespace', async () => {
  const source = await config();
  assert.match(source, /pattern\s*=\s*"https:\/\/api\.asterav8\.jp\/v1\/customer-ai\/\*"/);
  assert.match(source, /zone_name\s*=\s*"asterav8\.jp"/);
  assert.doesNotMatch(source, /pattern\s*=\s*"https:\/\/api\.asterav8\.jp\/\*"/);
});

test('existing KV binding name and only unresolved account ID remain explicit', async () => {
  const source = await config();
  assert.match(source, /binding\s*=\s*"CUSTOMER_AI_RESULTS"/);
  assert.match(source, /id\s*=\s*"replace_with_production_kv_namespace_id"/);
  assert.doesNotMatch(source, /replace_with_preview_kv_namespace_id/);
});

test('all required Worker secrets are declared without storing values', async () => {
  const source = await config();
  for (const name of [
    'WEBHOOK_INTERNAL_API_URL',
    'WEBHOOK_INTERNAL_API_TOKEN',
    'RESULT_WEBHOOK_SECRET',
    'TURNSTILE_SECRET',
    'CUSTOMER_AI_EXTERNAL_API_TOKEN'
  ]) {
    assert.match(source, new RegExp(`"${name}"`));
  }
  assert.doesNotMatch(source, /Bearer\s+[A-Za-z0-9._-]{16,}/);
});
