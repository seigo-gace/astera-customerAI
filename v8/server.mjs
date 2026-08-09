import net from "node:net";
import fs from "node:fs";

const socketPath = process.env.CUSTOMER_AI_NODE_SOCKET || "/tmp/customer-ai-v8.sock";
try { fs.unlinkSync(socketPath); } catch (error) { if (error.code !== "ENOENT") throw error; }

const TOPICS = [
  ["credit", /クレジット|残高|credit|付与|反映/i],
  ["account", /アカウント|ログイン|パスワード|認証|account|login|退会|解約/i],
  ["billing", /料金|支払|決済|プラン|請求|billing|payment|price/i],
  ["webhook-gateway", /webhook|配送|再送|リプレイ|届か/i],
  ["astera-app", /astera[- ]?app|アステラアプリ/i],
  ["astera", /astera|アステラ/i],
  ["api", /api|キー|key|エンドポイント|連携/i],
  ["corporate", /法人|スポンサー|投資|提携|enterprise|sponsor/i],
];

const INTENTS = [
  ["comparison", /違い|比較|どちら|何が異なる|\bvs\b/i],
  ["troubleshooting", /エラー|動かない|できない|反映されない|届かない|失敗|不具合|直し/i],
  ["procedure", /方法|手順|どうやって|どこから|どこを|何を確認|確認すれば|確認方法|設定|使い方|始め方|導入/i],
  ["pricing", /料金|価格|いくら|費用|課金|クレジット/i],
  ["contract", /契約|解約|退会|返金|更新|支払/i],
  ["availability", /使える|使えます|使えない|利用でき|対応|可能|できる|未実装|提供/i],
  ["limitation", /制限|上限|できない|禁止|対象外|条件/i],
  ["definition", /とは|何ですか|何なの|意味|概要/i],
];

const EVIDENCE_BY_INTENT = {
  comparison: ["definition", "responsibility", "limitations"],
  troubleshooting: ["symptom", "cause", "check", "resolution", "completion_check"],
  procedure: ["prerequisites", "ordered_steps", "completion_check"],
  pricing: ["current_price", "conditions", "effective_date"],
  contract: ["current_terms", "conditions", "ordered_steps"],
  availability: ["implementation_status", "conditions", "limitations"],
  limitation: ["answer_boundary", "conditions", "exceptions"],
  definition: ["definition", "purpose", "scope"],
  general: ["confirmed_answer", "conditions"],
};

const SHAPE_BY_INTENT = {
  comparison: "comparison",
  troubleshooting: "resolution_steps",
  procedure: "ordered_steps",
  pricing: "current_fact",
  contract: "conditions_and_steps",
  availability: "yes_no_with_conditions",
  limitation: "boundary",
  definition: "conclusion_and_detail",
  general: "conclusion_and_detail",
};

const STOP_WORDS = new Set([
  "これ", "それ", "その", "この", "あれ", "あと", "また", "さらに", "について", "教えて", "ください",
  "です", "ます", "したい", "できる", "できます", "どう", "どこ", "何", "なぜ", "場合", "もの", "こと",
]);

const TASK_MODIFIER_PATTERNS = [
  /^(?:同じ件(?:です)?|同じ話(?:です)?|先ほどの件(?:です)?)$/,
  /^(?:条件を変え(?:ます)?|条件変更(?:です)?)$/,
  /^(?:結論から|要点だけ|簡潔に|短く|詳しく|もう少し詳しく|わかりやすく)(?:教えて(?:ください)?)?$/,
  /^(?:誤解しやすい点も含めて|注意点も含めて|例外も含めて)(?:教えて(?:ください)?)?$/,
  /^(?:スマホ|モバイル|Android|iPhone|PC|デスクトップ|タブレット)(?:利用者)?(?:です)?$/i,
  /^(?:初心者|一般利用者|登録利用者|開発者|法人)(?:向け)?(?:です)?$/,
];

function normalize(text) {
  return String(text || "")
    .normalize("NFKC")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/[\t ]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function detectTopic(message) {
  for (const [topic, pattern] of TOPICS) if (pattern.test(message)) return topic;
  return "";
}

function detectIntent(message) {
  for (const [intent, pattern] of INTENTS) if (pattern.test(message)) return intent;
  return "general";
}

function detectAudience(message, source) {
  if (/開発者|実装|コード|sdk|api|webhook|json|http|github/i.test(message)) return "developer";
  if (/法人|企業|スポンサー|投資|提携/i.test(message)) return "business";
  return source === "astera-app" ? "registered_user" : "general_user";
}

function isTaskModifier(text) {
  const value = normalize(text).replace(/[。?？!！\s]+$/g, "").trim();
  return TASK_MODIFIER_PATTERNS.some((pattern) => pattern.test(value));
}

function extractDetails(message) {
  const details = {};
  const hour = message.match(/(?:午前|午後)?\s*(\d{1,2})\s*時/);
  if (hour) details.approximate_hour = Number(hour[1]);
  const errorCode = message.match(/(?:エラー|error)\s*[:：#-]?\s*([A-Z0-9_-]{3,40})/i);
  if (errorCode) details.error_code = errorCode[1];
  const timing = message.match(/今日|昨日|一昨日|今朝|昨夜|さっき|先ほど/);
  if (timing) details.relative_time = timing[0];
  if (/スマホ|モバイル|android|iphone/i.test(message)) details.interface = "mobile";
  else if (/pc|デスクトップ/i.test(message)) details.interface = "desktop";
  else if (/タブレット/i.test(message)) details.interface = "tablet";
  if (/結論から|要点だけ|簡潔に|短く/.test(message)) details.response_style = "conclusion_first";
  if (/詳しく|詳細/.test(message)) details.detail_level = "detailed";
  return details;
}

function splitDocument(message) {
  const lines = normalize(message)
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(?:[-*・]|\d+[.)．、]|[①-⑳])\s*/, "").trim())
    .filter(Boolean);
  const source = lines.length ? lines : [normalize(message)];
  const candidates = [];
  for (const line of source) {
    const sentences = line.split(/(?<=[?？!！。])\s*/).filter(Boolean);
    for (const sentence of sentences) {
      for (const part of sentence.split(/\s*(?:それと|さらに|加えて|あと、|また、)\s*/)) {
        const raw = part.trim();
        if (!raw) continue;
        const questionMark = /[?？]$/.test(raw);
        const cleaned = raw.replace(/[。?？\s]+$/g, "").trim();
        if (!cleaned || isTaskModifier(cleaned)) continue;
        const actionable = questionMark || detectIntent(cleaned) !== "general" || /教えて|知りたい|確認したい/.test(cleaned);
        candidates.push({ text: cleaned, actionable });
      }
    }
  }
  const actionable = candidates.filter((item) => item.actionable).map((item) => item.text);
  const selected = actionable.length ? actionable : candidates.map((item) => item.text);
  return [...new Set(selected)].slice(0, 8).length ? [...new Set(selected)].slice(0, 8) : [normalize(message)];
}

function searchTerms(text, subject, intent, activeTopic) {
  const working = normalize(text)
    .replace(/(?:について|という|できますか|できるか|教えてください|教えて|とは|何ですか|どうですか)/g, " ")
    .replace(/[?？!！。、,/:：()（）[\]「」『』-]+/g, " ")
    .replace(/(?:は|が|を|に|で|と|や|へ|から|まで|より|の)/g, " ");
  const candidates = working.match(/[A-Za-z][A-Za-z0-9_.-]{1,40}|[一-龯ぁ-んァ-ヶー]{2,24}/g) || [];
  const seeded = [subject, activeTopic, intent, ...candidates];
  const result = [];
  for (const raw of seeded) {
    const value = normalize(raw).toLowerCase();
    if (!value || value === "general" || STOP_WORDS.has(value) || result.includes(value)) continue;
    result.push(value);
  }
  return result.slice(0, 12);
}

function questionTasks(message, context, source, activeTopic) {
  const previousTopic = context.active_topic || activeTopic || "";
  return splitDocument(message).map((text, index) => {
    const subject = detectTopic(text) || previousTopic || "general";
    const intent = detectIntent(text);
    return {
      task_id: `q${index + 1}`,
      text,
      subject,
      intent,
      audience: detectAudience(text, source),
      answer_shape: SHAPE_BY_INTENT[intent] || SHAPE_BY_INTENT.general,
      search_terms: searchTerms(text, subject, intent, activeTopic),
      required_evidence: EVIDENCE_BY_INTENT[intent] || EVIDENCE_BY_INTENT.general,
      depends_on: [],
    };
  });
}

function analyzeTurn(payload) {
  const message = normalize(payload.message);
  const context = payload.context || {};
  const source = payload.source || "astera-hp";
  const explicitTopic = detectTopic(message);
  const followUp = /^(それ|その|これ|では|じゃあ|あと|他|ちなみに|で、|なぜ|何で|どう|いつ|どこ|さっき|先ほど|同じ件|同じ話|条件を変え)/.test(message)
    || (!explicitTopic && Boolean(context.active_topic || context.user_goal));
  const activeTopic = explicitTopic || context.active_topic || "general";
  const newTopic = Boolean(explicitTopic && context.active_topic && explicitTopic !== context.active_topic && !followUp);
  const userGoal = newTopic || !context.user_goal ? message : context.user_goal;
  const unresolved = Array.isArray(context.unresolved_questions) ? context.unresolved_questions.slice(-5) : [];
  const tasks = questionTasks(message, context, source, activeTopic);
  const retrievalParts = tasks.flatMap((task) => task.search_terms);
  if (followUp && userGoal && userGoal !== message) retrievalParts.push(userGoal);
  if (activeTopic && activeTopic !== "general") retrievalParts.push(activeTopic);
  retrievalParts.push(...unresolved);
  const retrievalQuery = [...new Set(retrievalParts.map(normalize).filter(Boolean))].join(" ").slice(0, 1200);
  const conditions = { ...(context.confirmed_details || {}), ...extractDetails(message) };
  return {
    message,
    follow_up: followUp,
    context_used: Boolean(context.user_goal || (context.turns || []).length),
    explicit_topic: explicitTopic,
    active_topic: activeTopic,
    user_goal: userGoal,
    new_topic: newTopic,
    confirmed_details: conditions,
    retrieval_query: retrievalQuery,
    question_count: tasks.length,
    question_tasks: tasks,
    analysis_dictionary: {
      purpose: userGoal,
      targets: [...new Set(tasks.map((task) => task.subject))],
      conditions,
      constraints: [
        "use_confirmed_kb_only_for_product_facts",
        "do_not_claim_unexecuted_actions",
        "answer_every_detected_question_or_mark_it_unresolved",
      ],
      missing_information: [],
      premises: Object.keys(context.confirmed_details || {}),
      uncertainty: [],
    },
  };
}

function verifyTurn(payload) {
  const answer = normalize(payload.answer);
  const violations = [];
  const availableEvidenceIds = new Set(payload.available_evidence_ids || []);
  const questionTaskIds = new Set(payload.question_task_ids || []);
  const answeredTaskIds = new Set(payload.answered_task_ids || []);
  const unresolvedTaskIds = new Set(payload.unresolved_task_ids || []);
  const usedEvidenceIds = new Set(payload.used_evidence_ids || []);

  if (!answer) violations.push("empty_answer");
  for (const evidenceId of usedEvidenceIds) {
    if (!availableEvidenceIds.has(evidenceId)) violations.push("unknown_evidence_reference");
  }
  for (const taskId of [...answeredTaskIds, ...unresolvedTaskIds]) {
    if (!questionTaskIds.has(taskId)) violations.push("unknown_task_reference");
  }
  for (const taskId of questionTaskIds) {
    if (!answeredTaskIds.has(taskId) && !unresolvedTaskIds.has(taskId)) violations.push("question_coverage_missing");
  }
  if (availableEvidenceIds.size && answeredTaskIds.size && !usedEvidenceIds.size) violations.push("evidence_grounding_missing");
  if (/情報がありません|回答できません|お問い合わせください|I (?:cannot|can't) answer/i.test(answer)) violations.push("generic_non_answer");
  if (/\b(?:as an ai|as a language model|qwen|hugging face|model provider)\b/i.test(answer)) violations.push("engine_identity");
  if (/(?:\/internal\/|src\/(?:system|component|feature|part)\/|\.env\b)/i.test(answer)) violations.push("internal_implementation");
  if (/(?:返金しました|削除しました|解約しました|処理しました|設定しました|refunded|deleted|cancelled)/i.test(answer)) violations.push("unverified_action_claim");
  const expectedTopic = payload.analysis?.active_topic || "";
  const returnedTopic = payload.returned_topic || expectedTopic;
  if (payload.analysis?.follow_up && expectedTopic && returnedTopic && expectedTopic !== returnedTopic) violations.push("conversation_topic_drift");
  return {
    answer,
    passed: violations.length === 0,
    violations: [...new Set(violations)],
    coverage: {
      question_task_ids: [...questionTaskIds],
      answered_task_ids: [...answeredTaskIds],
      unresolved_task_ids: [...unresolvedTaskIds],
      used_evidence_ids: [...usedEvidenceIds],
    },
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
