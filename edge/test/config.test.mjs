import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const configUrl = new URL('../wrangler.toml', import.meta.url);

async function config() {
  return readFile(configUrl, 'utf8');
}

test('Cloudflare route is HTTPS-only and limited to the Customer AI API namespace', async () => {
  const source = await config();
  assert.match(source, /pattern\s*=\s*"https:\/\/api\.asterav8\.jp\/v1\/customer-ai\/\*"/);
  assert.match(source, /zone_name\s*=\s*"asterav8\.jp"/);
  assert.doesNotMatch(source, /pattern\s*=\s*"https:\/\/api\.asterav8\.jp\/\*"/);
});

test('synchronous edge has no legacy job-result KV or webhook gateway bindings', async () => {
  const source = await config();
  assert.doesNotMatch(source, /CUSTOMER_AI_RESULTS/);
  assert.doesNotMatch(source, /WEBHOOK_INTERNAL_API_URL/);
  assert.doesNotMatch(source, /WEBHOOK_INTERNAL_API_TOKEN/);
  assert.doesNotMatch(source, /RESULT_WEBHOOK_SECRET/);
});

test('current private-runtime and Turnstile secrets are required without values', async () => {
  const source = await config();
  for (const name of [
    'CUSTOMER_AI_RUNTIME_URL',
    'CUSTOMER_AI_HMAC_SECRET',
    'HF_TOKEN',
    'TURNSTILE_SECRET',
    'TURNSTILE_SITE_KEY'
  ]) {
    assert.match(source, new RegExp(`"${name}"`));
  }
  assert.doesNotMatch(source, /(?:HF_TOKEN|CUSTOMER_AI_HMAC_SECRET|TURNSTILE_SECRET)\s*=\s*"[^"\n]+"/);
});

test('public limits and allowed origins stay explicit', async () => {
  const source = await config();
  assert.match(source, /MAX_MESSAGE_CHARS\s*=\s*"12000"/);
  assert.match(source, /RUNTIME_REQUEST_TIMEOUT_MS\s*=\s*"30000"/);
  assert.match(source, /ALLOWED_ORIGINS\s*=\s*"https:\/\/asterav8\.jp,https:\/\/www\.asterav8\.jp,https:\/\/app\.asterav8\.jp"/);
});
