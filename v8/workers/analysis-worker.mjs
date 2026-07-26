import { parentPort } from "node:worker_threads";

const SIGNALS = [
  { key: "urgency", re: /急ぎ|至急|今すぐ|早く|期限|今日中|urgent|asap|deadline/i, weight: 2 },
  { key: "anger", re: /ふざけ|怒|むかつ|舐め|使え|イラ|angry|mad/i, weight: 2 },
  { key: "fatigue", re: /疲|だる|しんど|つら|限界|寝不足|tired|exhausted/i, weight: 2 },
  { key: "confusion", re: /わから|不明|迷|混乱|どれ|何を|どうす|confused|unknown/i, weight: 2 },
  { key: "precision", re: /正確|検証|根拠|事実|嘘|hallucination|verify/i, weight: 2 },
  { key: "scope_pressure", re: /全部|完璧|最大|一式|抜け漏れ|端折るな|all|complete|perfect/i, weight: 1 },
];

function normalize(payload) {
  const text = String(payload.message || "").normalize("NFKC").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").replace(/\s+/g, " ").trim();
  return { message: text, search_query: text, normalized_length: text.length };
}

function humanContext(payload) {
  const text = String(payload.message || "");
  const hits = SIGNALS.filter((signal) => signal.re.test(text));
  const load = hits.reduce((sum, signal) => sum + signal.weight, 0);
  const keys = hits.map((item) => item.key);
  let mode = "stable";
  if (load >= 5 || keys.includes("anger")) mode = "high_pressure";
  else if (keys.includes("confusion") || keys.includes("fatigue")) mode = "supportive";
  else if (keys.includes("precision")) mode = "audit";
  const responsePolicy = [
    mode === "high_pressure" ? "minimize_questions_and_fix_first" : "normal",
    keys.includes("precision") ? "separate_confirmed_inference_unknown" : null,
    keys.includes("scope_pressure") ? "split_core_additional_pending" : null,
  ].filter(Boolean);
  return { mode, load, signals: keys, response_policy: responsePolicy };
}

function route(payload) {
  const text = String(payload.message || "").toLowerCase();
  const routes = [
    ["credit", ["クレジット", "残高", "credit", "反映"]],
    ["account", ["ログイン", "アカウント", "password", "認証"]],
    ["webhook", ["webhook", "届か", "配送", "再送"]],
    ["api", ["api", "key", "キー"]],
    ["billing", ["料金", "支払", "決済", "plan", "プラン"]],
    ["cancel", ["解約", "退会", "削除", "cancel", "delete account"]],
    ["corporate", ["法人", "スポンサー", "投資", "提携", "enterprise", "sponsor"]],
  ];
  const scored = routes.map(([intent, words]) => ({ intent, score: words.filter((word) => text.includes(word)).length })).filter((item) => item.score > 0).sort((a, b) => b.score - a.score || a.intent.localeCompare(b.intent));
  return { intent: scored[0]?.intent || "general", alternatives: scored.slice(1, 4), ambiguity: scored.length > 1 && scored[0].score === scored[1].score ? 1 : 0 };
}

function decompose(payload) {
  const text = String(payload.message || "").trim();
  const items = text.split(/[?？\n]+/).map((item) => item.trim()).filter(Boolean);
  return { sub_questions: items.length ? items : [text] };
}

function entities(payload) {
  const text = String(payload.message || "");
  const result = {};
  const time = text.match(/(?:午前|午後)?\s*(\d{1,2})\s*時/);
  if (time) result.approximate_hour = Number(time[1]);
  const code = text.match(/(?:エラー|error)\s*[:：#-]?\s*([A-Z0-9_-]{3,40})/i);
  if (code) result.error_code = code[1];
  return { entities: result };
}

function safety(payload) {
  const text = String(payload.message || "");
  const overlays = [];
  if (/料金|価格|現在|最新|today|current|price/i.test(text)) overlays.push("current_information");
  if (/根拠|証拠|正確|検証|evidence|verify/i.test(text)) overlays.push("evidence_strict");
  if (/password|api[_ -]?key|secret|token|カード|card/i.test(text)) overlays.push("secret_sensitive");
  return { overlays, fail_closed: overlays.includes("secret_sensitive") };
}

const handlers = { normalize, human_context: humanContext, route, decompose, entities, safety };

parentPort.on("message", ({ id, workerName, payload }) => {
  try {
    const handler = handlers[workerName];
    if (!handler) throw new Error(`unsupported_worker:${workerName}`);
    parentPort.postMessage({ id, ok: true, result: handler(payload) });
  } catch (error) {
    parentPort.postMessage({ id, ok: false, error: String(error?.message || error) });
  }
});
