import net from "node:net";
import fs from "node:fs";
import { WorkerPool } from "./worker-pool.mjs";

const socketPath = process.env.CUSTOMER_AI_NODE_SOCKET || "/tmp/customer-ai-v8.sock";
const poolSize = Math.max(1, Math.min(4, Number(process.env.CUSTOMER_AI_V8_WORKER_POOL_SIZE || 2)));
try { fs.unlinkSync(socketPath); } catch (error) { if (error.code !== "ENOENT") throw error; }

const pool = new WorkerPool({ size: poolSize, timeoutMs: 10_000 });

function normalizeText(text) {
  return String(text || "").normalize("NFKC").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim();
}

async function analyze(payload) {
  const workerNames = ["normalize", "human_context", "route", "decompose", "entities", "safety"];
  const results = await Promise.all(workerNames.map((name) => pool.exec(name, payload)));
  const [normalized, humanState, routed, decomposed, extracted, safety] = results;
  return {
    message: normalized.message,
    search_query: normalized.search_query,
    normalized_length: normalized.normalized_length,
    human_state: humanState,
    intent: routed.intent,
    alternatives: routed.alternatives,
    ambiguity: routed.ambiguity,
    sub_questions: decomposed.sub_questions,
    entities: extracted.entities,
    safety,
    worker_results: workerNames,
  };
}

function plan(payload) {
  const analysis = payload.analysis || {};
  const evidence = Array.isArray(payload.evidence) ? payload.evidence : [];
  const renderer = payload.renderer || {};
  const contract = payload.contract || {};
  const unresolvedAmbiguity = Number(analysis.ambiguity || 0) > 0;
  const multiQuestion = (analysis.sub_questions || []).length > 1;
  const multiEvidence = evidence.length > 1;
  const humanAdaptation = ["high_pressure", "supportive"].includes(analysis.human_state?.mode);
  const deterministicIncomplete = !String(payload.draft || "").trim() || Boolean(renderer.clarification);
  const engineRequired = Boolean(
    contract.engine_policy !== undefined
    && renderer.requires_language_engine
    && !deterministicIncomplete
    && (unresolvedAmbiguity || (multiQuestion && multiEvidence) || (humanAdaptation && multiEvidence))
  );
  return {
    engine_required: engineRequired,
    engine_reason: engineRequired ? "structured_multi_evidence_composition" : "deterministic_skills_sufficient",
    missing_values: [],
    clarification: renderer.clarification || null,
    action: null,
    required_evidence_ids: evidence.map((item) => item.evidence_id).filter(Boolean),
    required_question_indexes: (analysis.sub_questions || []).map((_, index) => index),
    stop_conditions: contract.stop_conditions || [],
  };
}

function verify(payload) {
  let answer = normalizeText(payload.answer);
  const violations = [];
  const evidenceIds = new Set((payload.evidence || []).map((item) => item.evidence_id).filter(Boolean));
  const usedEvidenceIds = new Set(payload.engine_output?.used_evidence_ids || payload.renderer?.evidence_refs || []);
  for (const evidenceId of usedEvidenceIds) if (!evidenceIds.has(evidenceId)) violations.push("unknown_evidence_reference");
  const forbidden = [
    [/\b(?:password|api[_ -]?key|secret|token)\b\s*[:=]\s*\S+/i, "secret_pattern"],
    [/(?:\/internal\/|src\/(?:system|component|feature|part)\/|\.env\b)/i, "internal_implementation"],
    [/(?:完了しました|成功しました|返金しました|削除しました|refunded|deleted)/i, "unverified_action_claim"],
    [/\b(?:as an ai|as a language model|qwen|hugging face|model provider)\b/i, "engine_identity"],
  ];
  for (const [pattern, code] of forbidden) {
    if (pattern.test(answer)) {
      violations.push(code);
      answer = answer.replace(pattern, "確認済みの範囲で案内します");
    }
  }
  if (!answer) violations.push("empty_answer");
  const requiredQuestions = payload.plan?.required_question_indexes || [];
  const coveredQuestions = payload.engine_output?.covered_question_indexes || payload.renderer?.covered_question_indexes || [];
  const missing = requiredQuestions.filter((index) => !coveredQuestions.includes(index));
  if (missing.length && payload.evidence?.length) violations.push("question_coverage_incomplete");
  const blocking = violations.filter((code) => code !== "engine_identity");
  return {
    answer: answer || (payload.request?.locale === "ja-JP" ? "確認できる情報が不足しています。" : "Confirmed information is insufficient."),
    violations: [...new Set(violations)],
    completion: { passed: blocking.length === 0 && missing.length === 0 && Boolean(answer), missing: missing.map((index) => `sub_question:${index}`) },
  };
}

async function handle(request) {
  const started = Date.now();
  const { request_id: requestId, phase, payload, deadline_at: deadlineAt } = request;
  if (!requestId || !phase || !payload) throw new Error("invalid_request");
  if (Number(deadlineAt || 0) < Date.now()) throw new Error("deadline_exceeded");
  let result;
  if (phase === "analyze") result = await analyze(payload);
  else if (phase === "plan") result = plan(payload);
  else if (phase === "verify") result = verify(payload);
  else if (phase === "ping") result = { pong: true, node: process.version, worker_pool: pool.status() };
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

async function shutdown() {
  server.close();
  await pool.destroy();
  try { fs.unlinkSync(socketPath); } catch {}
  process.exit(0);
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => { void shutdown(); });
