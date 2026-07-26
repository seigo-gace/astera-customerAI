import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const socket = `/tmp/astera-customer-ai-test-${process.pid}.sock`;
let child;

test.before(async () => {
  child = spawn(process.execPath, [path.join(root, "v8/server.mjs")], { env: { ...process.env, CUSTOMER_AI_NODE_SOCKET: socket } });
  for (let i = 0; i < 50 && !fs.existsSync(socket); i += 1) await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(fs.existsSync(socket), true);
});

test.after(() => {
  child?.kill("SIGTERM");
  try { fs.unlinkSync(socket); } catch {}
});

function request(phase, payload) {
  return new Promise((resolve, reject) => {
    const client = net.createConnection(socket);
    let data = "";
    client.on("connect", () => client.write(JSON.stringify({ request_id: "req-1", phase, deadline_at: Date.now() + 5000, payload }) + "\n"));
    client.on("data", (chunk) => { data += chunk; });
    client.on("end", () => resolve(JSON.parse(data)));
    client.on("error", reject);
  });
}

test("preprocess extracts intent and entity", async () => {
  const response = await request("preprocess", { message: "21時頃に買ったクレジットが反映されない", locale: "ja-JP" });
  assert.equal(response.ok, true);
  assert.equal(response.result.intent, "credit");
  assert.equal(response.result.entities.approximate_hour, 21);
});

test("verify removes internal implementation patterns", async () => {
  const response = await request("verify", { answer: "Use /internal/admin and secret=abc", locale: "en" });
  assert.equal(response.ok, true);
  assert.ok(response.result.violations.includes("internal_implementation"));
});
