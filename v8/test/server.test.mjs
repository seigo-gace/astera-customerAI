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
  child = spawn(process.execPath, [path.join(root, "v8/server.mjs")], { env: { ...process.env, CUSTOMER_AI_NODE_SOCKET: socket } });
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

test("follow-up analysis reuses cached goal and topic", async () => {
  const response = await request("analyze_turn", {
    message: "昨日の夜です。どこを確認すればいい？",
    context: {
      user_goal: "購入したクレジットが反映されない問題を解決する",
      active_topic: "credit",
      confirmed_details: {},
      unresolved_questions: [],
      turns: [{ role: "user", text: "購入したクレジットが反映されません" }],
    },
  });
  assert.equal(response.ok, true);
  assert.equal(response.result.follow_up, true);
  assert.equal(response.result.context_used, true);
  assert.equal(response.result.active_topic, "credit");
  assert.match(response.result.retrieval_query, /クレジット/);
  assert.equal(response.result.confirmed_details.relative_time, "昨日");
});

test("new explicit topic can replace the previous topic", async () => {
  const response = await request("analyze_turn", {
    message: "次はアカウント削除について教えて",
    context: { user_goal: "クレジット未反映を解決する", active_topic: "credit", turns: [] },
  });
  assert.equal(response.ok, true);
  assert.equal(response.result.active_topic, "account");
  assert.equal(response.result.new_topic, true);
});

test("verification rejects topic drift and unsafe claims", async () => {
  const response = await request("verify_turn", {
    answer: "As an AI, /internal/admin で削除しました",
    analysis: { follow_up: true, active_topic: "credit" },
    returned_topic: "account",
    used_kb_ids: [],
    available_kb_ids: [],
  });
  assert.equal(response.ok, true);
  assert.equal(response.result.passed, false);
  assert.ok(response.result.violations.includes("conversation_topic_drift"));
  assert.ok(response.result.violations.includes("internal_implementation"));
  assert.ok(response.result.violations.includes("unverified_action_claim"));
});
