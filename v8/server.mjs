import net from "node:net";
import fs from "node:fs";

const socketPath = process.env.CUSTOMER_AI_NODE_SOCKET || "/tmp/customer-ai-v8.sock";
try { fs.unlinkSync(socketPath); } catch (error) { if (error.code !== "ENOENT") throw error; }

const TOPICS = [
  ["credit", /クレジット|残高|credit|付与|反映/i],
  ["account", /アカウント|ログイン|パスワード|認証|account|login/i],
  ["billing", /料金|支払|決済|プラン|請求|billing|payment|price/i],
  ["cancel", /解約|退会|削除|cancel|delete account/i],
  ["webhook", /webhook|配送|再送|届か/i],
  ["api", /api|キー|key/i],
  ["corporate", /法人|スポンサー|投資|提携|enterprise|sponsor/i],
];

function normalize(text) {
  return String(text || "")
    .normalize("NFKC")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function detectTopic(message) {
  for (const [topic, pattern] of TOPICS) if (pattern.test(message)) return topic;
  return "";
}

function extractDetails(message) {
  const details = {};
  const hour = message.match(/(?:午前|午後)?\s*(\d{1,2})\s*時/);
  if (hour) details.approximate_hour = Number(hour[1]);
  const errorCode = message.match(/(?:エラー|error)\s*[:：#-]?\s*([A-Z0-9_-]{3,40})/i);
  if (errorCode) details.error_code = errorCode[1];
  const timing = message.match(/今日|昨日|一昨日|今朝|昨夜|さっき|先ほど/);
  if (timing) details.relative_time = timing[0];
  return details;
}

function analyzeTurn(payload) {
  const message = normalize(payload.message);
  const context = payload.context || {};
  const explicitTopic = detectTopic(message);
  const followUp = /^(それ|その|これ|では|じゃあ|あと|他|ちなみに|で、|なぜ|何で|どう|いつ|どこ|さっき|先ほど)/.test(message)
    || (!explicitTopic && Boolean(context.active_topic || context.user_goal));
  const activeTopic = explicitTopic || context.active_topic || "general";
  const newTopic = Boolean(explicitTopic && context.active_topic && explicitTopic !== context.active_topic && !followUp);
  const userGoal = newTopic || !context.user_goal ? message : context.user_goal;
  const unresolved = Array.isArray(context.unresolved_questions) ? context.unresolved_questions.slice(-3) : [];
  const retrievalParts = [message];
  if (followUp && userGoal && userGoal !== message) retrievalParts.push(userGoal);
  if (activeTopic && activeTopic !== "general") retrievalParts.push(activeTopic);
  retrievalParts.push(...unresolved);
  const retrievalQuery = [...new Set(retrievalParts.map(normalize).filter(Boolean))].join(" ").slice(0, 1200);
  const questionCount = message.split(/[?？]/).filter((item) => item.trim()).length;
  return {
    message,
    follow_up: followUp,
    context_used: Boolean(context.user_goal || (context.turns || []).length),
    explicit_topic: explicitTopic,
    active_topic: activeTopic,
    user_goal: userGoal,
    new_topic: newTopic,
    confirmed_details: { ...(context.confirmed_details || {}), ...extractDetails(message) },
    retrieval_query: retrievalQuery,
    question_count: Math.max(1, questionCount),
  };
}

function verifyTurn(payload) {
  let answer = normalize(payload.answer);
  const violations = [];
  const availableKbIds = new Set(payload.available_kb_ids || []);
  for (const kbId of payload.used_kb_ids || []) {
    if (!availableKbIds.has(kbId)) violations.push("unknown_kb_reference");
  }
  if (!answer) violations.push("empty_answer");
  if (/\b(?:as an ai|as a language model|qwen|hugging face|model provider)\b/i.test(answer)) violations.push("engine_identity");
  if (/(?:\/internal\/|src\/(?:system|component|feature|part)\/|\.env\b)/i.test(answer)) violations.push("internal_implementation");
  if (/(?:返金しました|削除しました|解約しました|処理しました|refunded|deleted|cancelled)/i.test(answer)) violations.push("unverified_action_claim");
  const expectedTopic = payload.analysis?.active_topic || "";
  const returnedTopic = payload.returned_topic || expectedTopic;
  if (payload.analysis?.follow_up && expectedTopic && returnedTopic && expectedTopic !== returnedTopic) violations.push("conversation_topic_drift");
  return {
    answer,
    passed: violations.length === 0,
    violations: [...new Set(violations)],
  };
}

async function handle(request) {
  const started = Date.now();
  const { request_id: requestId, phase, payload, deadline_at: deadlineAt } = request;
  if (!requestId || !phase || payload === undefined) throw new Error("invalid_request");
  if (Number(deadlineAt || 0) < Date.now()) throw new Error("deadline_exceeded");
  let result;
  if (phase === "analyze_turn") result = analyzeTurn(payload);
  else if (phase === "verify_turn") result = verifyTurn(payload);
  else if (phase === "ping") result = { pong: true, node: process.version };
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
      connection.end(JSON.stringify(await handle(JSON.parse(raw))) + "\n");
    } catch (error) {
      const requestId = (() => { try { return JSON.parse(raw).request_id || "unknown"; } catch { return "unknown"; } })();
      connection.end(JSON.stringify({ request_id: requestId, ok: false, result: {}, error_code: String(error?.message || error), duration_ms: 0 }) + "\n");
    }
  });
});

server.listen(socketPath, () => fs.chmodSync(socketPath, 0o600));

function shutdown() {
  server.close();
  try { fs.unlinkSync(socketPath); } catch {}
  process.exit(0);
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, shutdown);
