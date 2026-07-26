import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const socket = `/tmp/customer-ai-test-${process.pid}.sock`;
let child;

test.before(async () => {
  child = spawn(process.execPath, [path.join(root, "v8/server.mjs")], { env: { ...process.env, CUSTOMER_AI_NODE_SOCKET: socket, CUSTOMER_AI_V8_WORKER_POOL_SIZE: "2" } });
  for (let index = 0; index < 100 && !fs.existsSync(socket); index += 1) await new Promise((resolve) => setTimeout(resolve, 20));
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

test("analyze uses the persistent parallel worker pool", async () => {
  const response = await request("analyze", { message: "21時頃に買ったクレジットが反映されない。どうすればいい？", locale: "ja-JP" });
  assert.equal(response.ok, true);
  assert.equal(response.result.intent, "credit");
  assert.equal(response.result.entities.approximate_hour, 21);
  assert.deepEqual(response.result.worker_results, ["normalize", "human_context", "route", "decompose", "entities", "safety"]);
});

test("ping exposes worker-pool readiness", async () => {
  const response = await request("ping", {});
  assert.equal(response.ok, true);
  assert.equal(response.result.worker_pool.size, 2);
  assert.equal(response.result.worker_pool.workers, 2);
});

test("verify rejects internal implementation and engine identity leakage", async () => {
  const response = await request("verify", {
    answer: "As an AI, use /internal/admin and secret=abc",
    evidence: [],
    plan: { required_question_indexes: [] },
    renderer: { covered_question_indexes: [] },
    request: { locale: "en" },
  });
  assert.equal(response.ok, true);
  assert.ok(response.result.violations.includes("internal_implementation"));
  assert.ok(response.result.violations.includes("engine_identity"));
});
