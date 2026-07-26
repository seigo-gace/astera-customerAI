import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const socketPath = process.env.CUSTOMER_AI_NODE_SOCKET || "/tmp/astera-customer-ai-v8.sock";
try { fs.unlinkSync(socketPath); } catch (error) { if (error.code !== "ENOENT") throw error; }

let asteraEngine = null;
let asteraLoadError = null;
const asteraPath = process.env.CUSTOMER_AI_ASTERA_PATH || "";
if (asteraPath) {
  try {
    const require = createRequire(import.meta.url);
    const KaguraEngine = require(path.join(asteraPath, "src", "kagura-engine.js"));
    asteraEngine = new KaguraEngine({ poolSize: 1 });
  } catch (error) {
    asteraLoadError = String(error?.message || error);
  }
}

function normalize(text) {
  return String(text || "").normalize("NFKC").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim();
}

function preprocess(payload) {
  const message = normalize(payload.message);
  const lower = message.toLowerCase();
  const intents = [
    ["credit", ["クレジット", "残高", "credit", "反映"]],
    ["account", ["ログイン", "アカウント", "password", "認証"]],
    ["webhook", ["webhook", "届か", "配送", "再送"]],
    ["api", ["api", "key", "キー"]],
    ["billing", ["料金", "支払", "決済", "plan", "プラン"]],
  ];
  const intent = intents.find(([, words]) => words.some((word) => lower.includes(word)))?.[0] || "general";
  const emotion = /怒|ふざけ|困|最悪|反映されない|届かない|not working|broken/i.test(message) ? "frustrated" : "neutral";
  const subQuestions = message.split(/[?？\n]+/).map((item) => item.trim()).filter(Boolean);
  const entities = {};
  const timeMatch = message.match(/(?:午前|午後)?\s*(\d{1,2})\s*時/);
  if (timeMatch) entities.approximate_hour = Number(timeMatch[1]);
  return { message, intent, emotion, entities, sub_questions: subQuestions, search_query: message };
}

async function plan(payload) {
  const kb = Array.isArray(payload.kb) ? payload.kb : [];
  const preprocessResult = payload.preprocess || {};
  const message = normalize(payload.message);
  let astera = null;
  if (asteraEngine && (kb.length > 1 || preprocessResult.emotion === "frustrated" || /返金|解約|削除|責任/.test(message))) {
    try {
      const output = await asteraEngine.process({ question: message, context: JSON.stringify(kb), llm: { chain: ["null"] }, outputLanguage: payload.locale === "ja-JP" ? "ja" : "en" }, { id: "customer-ai" });
      astera = output?.material?.judgment || output?.result?.judgment || null;
    } catch (error) {
      astera = { error: String(error?.message || error) };
    }
  }
  const missingValues = [];
  const clarification = kb.length === 0 ? null : undefined;
  const aiRequired = kb.length > 1 || preprocessResult.emotion === "frustrated" || preprocessResult.sub_questions?.length > 1;
  return {
    ai_required: aiRequired,
    missing_values: missingValues,
    clarification,
    action: null,
    astera,
    astera_available: Boolean(asteraEngine),
    astera_load_error: asteraLoadError,
  };
}

function verify(payload) {
  let answer = normalize(payload.answer);
  const violations = [];
  const forbidden = [
    [/\b(?:password|api[_ -]?key|secret|token)\b\s*[:=]\s*\S+/i, "secret_pattern"],
    [/(?:\/internal\/|src\/(?:system|component|feature|part)\/|\.env\b)/i, "internal_implementation"],
    [/(?:完了しました|成功しました|refunded|deleted)/i, "unverified_action_claim"],
  ];
  for (const [pattern, code] of forbidden) {
    if (pattern.test(answer)) {
      violations.push(code);
      answer = answer.replace(pattern, "確認済みの範囲で案内します");
    }
  }
  if (!answer) {
    violations.push("empty_answer");
    answer = payload.locale === "ja-JP" ? "確認できる情報が不足しています。" : "Confirmed information is insufficient.";
  }
  return { answer, violations };
}

async function handle(request) {
  const started = Date.now();
  const { request_id: requestId, phase, payload, deadline_at: deadlineAt } = request;
  if (!requestId || !phase || !payload) throw new Error("invalid_request");
  if (Number(deadlineAt || 0) < Date.now()) throw new Error("deadline_exceeded");
  let result;
  if (phase === "preprocess") result = preprocess(payload);
  else if (phase === "plan") result = await plan(payload);
  else if (phase === "verify") result = verify(payload);
  else if (phase === "ping") result = { pong: true, node: process.version, astera_available: Boolean(asteraEngine) };
  else throw new Error("unsupported_phase");
  return { request_id: requestId, ok: true, result, error_code: null, duration_ms: Date.now() - started };
}

const server = net.createServer((connection) => {
  let buffer = "";
  connection.setEncoding("utf8");
  connection.on("data", async (chunk) => {
    buffer += chunk;
    if (buffer.length > 1_000_000) {
      connection.destroy(new Error("request_too_large"));
      return;
    }
    const newline = buffer.indexOf("\n");
    if (newline < 0) return;
    const raw = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    try {
      const response = await handle(JSON.parse(raw));
      connection.end(JSON.stringify(response) + "\n");
    } catch (error) {
      const requestId = (() => { try { return JSON.parse(raw).request_id || "unknown"; } catch { return "unknown"; } })();
      connection.end(JSON.stringify({ request_id: requestId, ok: false, result: {}, error_code: String(error?.message || error), duration_ms: 0 }) + "\n");
    }
  });
});

server.listen(socketPath, () => fs.chmodSync(socketPath, 0o600));
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
