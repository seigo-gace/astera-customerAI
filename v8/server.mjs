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
  ["incident", /障害|停止|落ち|接続でき|incident|outage/i],
];

const HUMAN_SIGNALS = [
  ["anger", /ふざけ|怒|むかつ|舐め|使え|イラ|angry|mad/i, 3],
  ["urgency", /急ぎ|至急|今すぐ|早く|今日中|urgent|asap/i, 2],
  ["confusion", /わから|不明|迷|混乱|どうすれば|confused|unknown/i, 2],
  ["precision", /正確|検証|根拠|事実|嘘|verify|evidence/i, 2],
  ["scope", /全部|すべて|完璧|抜け漏れ|端折るな|all|complete/i, 1],
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
  const paymentDisplay = message.match(/決済(?:済み|完了|失敗|保留)|支払い(?:済み|完了|失敗|保留)/);
  if (paymentDisplay) details.payment_display = paymentDisplay[0];
  const provider = message.match(/Stripe|Square|GitHub|Slack|Telegram|generic/i);
  if (provider) details.provider = provider[0];
  return details;
}

function humanContext(message) {
  const hits = HUMAN_SIGNALS.filter(([, pattern]) => pattern.test(message));
  const load = hits.reduce((sum, [, , weight]) => sum + weight, 0);
  const signals = hits.map(([name]) => name);
  let mode = "direct";
  if (signals.includes("anger") || load >= 5) mode = "fix_first";
  else if (signals.includes("confusion")) mode = "guided";
  else if (signals.includes("precision")) mode = "evidence_strict";
  return { mode, load, signals };
}

function splitQuestions(message) {
  const normalized = normalize(message);
  const segments = normalized
    .split(/[?？\n]+|(?<=。)/)
    .map((item) => item.replace(/。$/, "").trim())
    .filter(Boolean);
  const expanded = [];
  for (const segment of segments) {
    if (segment.length > 35 && /(?:それと|あと|また|さらに|及び|および)/.test(segment)) {
      expanded.push(...segment.split(/(?:それと|あと|また|さらに|及び|および)/).map((item) => item.trim()).filter(Boolean));
    } else {
      expanded.push(segment);
    }
  }
  return (expanded.length ? expanded : [normalized]).slice(0, 8).map((text, index) => ({ id: `q${index + 1}`, text, kind: "question" }));
}

function dynamicRequirements(topic, message) {
  const requirements = [];
  if (topic === "credit") {
    if (/反映|付与|残高|現在|いくつ|どれくらい/.test(message)) requirements.push("credit_balance", "credit_grant_status");
    if (/買|購入|支払|決済/.test(message)) requirements.push("payment_status");
  }
  if (topic === "billing") requirements.push("payment_status", "subscription_status");
  if (topic === "account") requirements.push("account_status");
  if (topic === "webhook") requirements.push("webhook_delivery_status");
  if (topic === "api" && /キー|key/i.test(message)) requirements.push("api_key_status");
  if (topic === "incident") requirements.push("service_incident_status");
  return [...new Set(requirements)].slice(0, 6);
}

function requiredInformation(topic, requirements, message, confirmedDetails) {
  const required = [];
  if (requirements.includes("payment_status") && confirmedDetails.approximate_hour === undefined && !confirmedDetails.relative_time) required.push("購入または決済した時刻");
  if (topic === "credit" && !confirmedDetails.payment_display && !/決済(?:済み|完了|失敗|保留)/.test(message)) required.push("決済画面に表示されている状態");
  if (topic === "webhook") {
    if (!confirmedDetails.provider) required.push("WebhookのProvider名");
    if (!confirmedDetails.relative_time && confirmedDetails.approximate_hour === undefined) required.push("送信した時刻");
  }
  if (/エラー|error/i.test(message) && !confirmedDetails.error_code) required.push("表示されているエラーコードまたは文面");
  return [...new Set(required)].slice(0, 8);
}

function retrievalQueries(message, questions, goal, topic, unresolved) {
  const queries = questions.map((question) => `${question.text} ${topic}`.trim());
  if (goal && goal !== message) queries.push(`${goal} ${topic}`.trim());
  for (const item of unresolved.slice(-3)) queries.push(`${item} ${topic}`.trim());
  return [...new Set(queries.map(normalize).filter(Boolean))].slice(0, 6);
}

async function prepareSupport(payload) {
  const message = normalize(payload.message);
  const context = payload.context || {};
  const explicitTopic = detectTopic(message);
  const followUp = /^(それ|その|これ|では|じゃあ|あと|他|ちなみに|で、|なぜ|何で|どう|いつ|どこ|さっき|先ほど)/.test(message)
    || (!explicitTopic && Boolean(context.active_topic || context.user_goal));
  const activeTopic = explicitTopic || context.active_topic || "general";
  const newTopic = Boolean(explicitTopic && context.active_topic && explicitTopic !== context.active_topic && !followUp);
  const userGoal = newTopic || !context.user_goal ? message : context.user_goal;
  const confirmedDetails = { ...(context.confirmed_details || {}), ...extractDetails(message) };
  const unresolved = Array.isArray(context.unresolved_questions) ? context.unresolved_questions.slice(-5) : [];

  const [questions, human, requirements] = await Promise.all([
    Promise.resolve(splitQuestions(message)),
    Promise.resolve(humanContext(message)),
    Promise.resolve(dynamicRequirements(activeTopic, message)),
  ]);
  const requiredInfo = requiredInformation(activeTopic, requirements, message, confirmedDetails);
  const complexity = requirements.length ? "dynamic" : questions.length > 1 ? "multi_question" : followUp ? "multi_turn" : human.mode !== "direct" ? "adapted" : "simple";
  return {
    message,
    follow_up: followUp,
    context_used: Boolean(context.user_goal || (context.turns || []).length),
    explicit_topic: explicitTopic,
    active_topic: activeTopic,
    user_goal: userGoal,
    new_topic: newTopic,
    confirmed_details: confirmedDetails,
    sub_questions: questions,
    retrieval_queries: retrievalQueries(message, questions, userGoal, activeTopic, unresolved),
    dynamic_requirements: requirements,
    required_information: requiredInfo,
    human_context: human,
    response_mode: human.mode,
    complexity,
    answer_order: human.mode === "fix_first"
      ? ["direct_answer", "current_status", "required_action", "reason", "exceptions"]
      : ["direct_answer", "reason", "steps", "exceptions", "next_action"],
  };
}

function verifySupport(payload) {
  const response = payload.response || {};
  const blueprint = payload.blueprint || {};
  const analysis = payload.analysis || {};
  const answer = normalize(response.answer);
  const violations = [];
  const availableEvidenceIds = new Set(blueprint.available_evidence_ids || []);
  const usedEvidenceIds = [...new Set(response.used_evidence_ids || [])];
  for (const evidenceId of usedEvidenceIds) if (!availableEvidenceIds.has(evidenceId)) violations.push("unknown_evidence_reference");
  if (!answer) violations.push("empty_answer");
  if (/\b(?:as an ai|as a language model|qwen|hugging face|model provider)\b/i.test(answer)) violations.push("engine_identity");
  if (/(?:\/internal\/|src\/(?:system|component|feature|part)\/|\.env\b)/i.test(answer)) violations.push("internal_implementation");
  if (/(?:返金しました|削除しました|解約しました|処理しました|refunded|deleted|cancelled)/i.test(answer)) violations.push("unverified_action_claim");

  const questionIds = new Set((blueprint.questions || []).map((item) => item.id));
  const answeredIds = new Set(response.answered_question_ids || []);
  const unresolved = Array.isArray(response.unresolved_questions) ? response.unresolved_questions.filter(Boolean) : [];
  const missingCoverage = [...questionIds].filter((id) => !answeredIds.has(id));
  if (missingCoverage.length > unresolved.length) violations.push("question_coverage_incomplete");
  if (unresolved.length && !(response.requested_information || []).length && !blueprint.pending_requirements?.length) violations.push("unresolved_without_next_information");
  if (!usedEvidenceIds.length && availableEvidenceIds.size && answeredIds.size) violations.push("grounding_missing");
  if (analysis.follow_up && response.active_topic && analysis.active_topic && response.active_topic !== analysis.active_topic) violations.push("conversation_topic_drift");
  if (analysis.follow_up && response.user_goal && analysis.user_goal && response.user_goal !== analysis.user_goal) violations.push("conversation_goal_drift");
  if (/確認できる情報が不足しています|詳しい情報を教えてください/.test(answer) && !(response.requested_information || []).length) violations.push("generic_non_answer");
  if ((blueprint.questions || []).length > 1 && answer.length < 80 && !unresolved.length) violations.push("multi_question_answer_too_short");

  return {
    answer,
    passed: violations.length === 0,
    violations: [...new Set(violations)],
    missing_question_ids: missingCoverage,
    repair_instructions: {
      answer_missing_questions: missingCoverage,
      use_only_evidence_ids: [...availableEvidenceIds],
      preserve_goal: analysis.user_goal,
      preserve_topic: analysis.active_topic,
      state_exact_missing_information: true,
    },
  };
}

async function handle(request) {
  const started = Date.now();
  const { request_id: requestId, phase, payload, deadline_at: deadlineAt } = request;
  if (!requestId || !phase || payload === undefined) throw new Error("invalid_request");
  if (Number(deadlineAt || 0) < Date.now()) throw new Error("deadline_exceeded");
  let result;
  if (phase === "prepare_support" || phase === "analyze_turn") result = await prepareSupport(payload);
  else if (phase === "verify_support" || phase === "verify_turn") result = verifySupport(payload);
  else if (phase === "ping") result = { pong: true, node: process.version, support_pipeline: "v2" };
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
