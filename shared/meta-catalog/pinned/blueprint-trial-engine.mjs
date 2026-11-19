// 钉住的蓝图裁决引擎（原样转录，非改写）
//
// 为什么钉一份：
//   fleet-packs 的 E3「假宇宙」要证的是「我们的 brief 声明，会被 paas 的真引擎判成绿」。
//   判据真源是 paas/server/blueprint-trial.js 的 evaluateTrial 与
//   paas/server/blueprint-validate.js 的 normalizeAcceptance——两者**函数体都是纯的**，
//   但所在文件顶部 import 了 cron/catalog/pack-store/provisioner/DB/llm-providers，
//   于是 `import` 一次就要整棵 paas 运行图（连 node_modules）。结果：E3 只能在作者机上绿，
//   换一棵没有 paas 邻居的干净 clone 就 ERR_MODULE_NOT_FOUND——那正是元线「可被任何人
//   在任何地方验证」这一条最要命的反例。
//
//   所以这里只转录**纯的那两段**（+ 它们依赖的三个常量），不碰运行图。
//   这份是副本，副本会烂——因此 scripts/trial_fake_universe.mjs 带一条漂移臂：
//   只要机器上能解析到 paas 检出，就把同一批 15 个例子在两份引擎上各跑一遍，
//   逐条比 verdicts，并比断言闭集与 llm-judged 上限；任何一处不同即判红并点名。
//   没有 paas 检出时，脚本会明说「漂移未验」——而不是静默当绿。
//
// 出处（2026-11-13 转录）：
//   repo     paas（工作区同级仓）
//   commit   9905c54fa57e5b0db0d7d270e5f27b2234745c06
//   file     server/blueprint-trial.js      blob 915cadca38de0b651db6fc69bb4fcf14a3562e23
//            └ evaluateTrial（第 34-101 行）、membersFor（第 28-31 行）
//   file     server/blueprint-validate.js   blob d66d1e6040273fc9f1cdffc2e5dc0001038b880f
//            └ MACHINE_ASSERTION_KINDS（214 行）、TRIAL_ASSERTION_KINDS（217 行）、
//              JUDGE_ASSERTION_CAP（220 行）、normalizeAcceptance（第 222-267 行）
//
// 转录纪律：
//   · 函数体逐字照抄，连注释与文案都不改——判错时 detail 文案要和线上同形，否则「哪条红了」
//     的对照会对不上。
//   · 有意**不**转录的：llm-judged 的 rubric 判定（那条要真模型，E3 明令零真模型；
//     evaluateTrial 本身对未知 kind 兜底判不绿，与线上一致）。
//   · 修改这份文件等于修改判据。别手改：重跑 scripts/trial_fake_universe.mjs 的漂移臂，
//     它会把「钉的」和「线上的」差异点名给你；真落后了就按上面的出处重新转录。

export const PROVENANCE = {
  repo: "paas",
  commit: "9905c54fa57e5b0db0d7d270e5f27b2234745c06",
  files: {
    "server/blueprint-trial.js": "915cadca38de0b651db6fc69bb4fcf14a3562e23",
    "server/blueprint-validate.js": "d66d1e6040273fc9f1cdffc2e5dc0001038b880f",
  },
  transcribedAt: "2026-11-13",
};

// ── 以下转录自 server/blueprint-validate.js ────────────────────────────────

export const MACHINE_ASSERTION_KINDS = ["post-materialized", "first-round-fired", "report-landed", "escalate-bounded"];

export const TRIAL_ASSERTION_KINDS = [...MACHINE_ASSERTION_KINDS, "llm-judged"];

export const JUDGE_ASSERTION_CAP = Number(process.env.BLUEPRINT_JUDGE_MAX || 8);

export function normalizeAcceptance(acceptance) {
  const list = Array.isArray(acceptance) ? acceptance : [];
  const assertions = [];
  const errors = [];
  list.forEach((item, i) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      errors.push({ error: `acceptance[${i}] must be an object of { id, kind, target?, expect? }`, entry: `acceptance[${i}]`, code: "acceptance_entry_invalid" });
      return;
    }
    const id = typeof item.id === "string" && item.id.trim() ? item.id : `a${i + 1}`;
    const kind = item.kind;
    if (!TRIAL_ASSERTION_KINDS.includes(kind)) {
      errors.push({ error: `acceptance[${i}] '${id}' declares unsupported assertion kind '${kind ?? "(none)"}' (supported: ${TRIAL_ASSERTION_KINDS.join(", ")})`, entry: `acceptance[${i}].kind`, code: "acceptance_kind_unsupported" });
    }
    if (item.target !== undefined && typeof item.target !== "string") {
      errors.push({ error: `acceptance[${i}] '${id}' target must be a post title when present`, entry: `acceptance[${i}].target`, code: "acceptance_target_invalid" });
    }
    if (kind === "escalate-bounded" && item.expect !== undefined && !Number.isFinite(item.expect)) {
      errors.push({ error: `acceptance[${i}] '${id}' expect must be a number for escalate-bounded`, entry: `acceptance[${i}].expect`, code: "acceptance_expect_invalid" });
    }
    // llm-judged：rubric 是判据原文，必须非空——空判据等于没判据。
    let rubric = null;
    if (kind === "llm-judged") {
      if (typeof item.rubric !== "string" || !item.rubric.trim()) {
        errors.push({
          error: `acceptance[${i}] '${id}' llm-judged needs a non-empty rubric (the acceptance text the judge reads)`,
          entry: `acceptance[${i}].rubric`,
          code: "acceptance_rubric_required",
        });
      } else {
        rubric = item.rubric.trim();
      }
    }
    assertions.push({ id, kind, target: item.target ?? null, expect: item.expect ?? null, ...(rubric ? { rubric } : {}) });
  });
  const judged = assertions.filter((a) => a.kind === "llm-judged");
  if (judged.length > JUDGE_ASSERTION_CAP) {
    errors.push({
      error: `this blueprint declares ${judged.length} llm-judged assertions, over the cap of ${JUDGE_ASSERTION_CAP} (each one is a model call) — merge or drop some`,
      entry: "acceptance",
      code: "acceptance_judge_cap",
    });
  }
  return { assertions, errors };
}

// ── 以下转录自 server/blueprint-trial.js ───────────────────────────────────

function membersFor(assertion, members) {
  if (!assertion.target) return members;
  return members.filter((m) => m.title === assertion.target);
}

// 逐断言判绿并附证据三元组（turnIds / reportIds / sourceRefs）。
export function evaluateTrial({ assertions, evidence, since }) {
  return assertions.map((a) => {
    const members = membersFor(a, evidence.members ?? []);
    const memberIds = new Set(members.map((m) => m.memberId));
    const evidenceOut = { turnIds: [], reportIds: [], sourceRefs: [] };
    const inWindow = (at) => typeof at === "number" && at >= since;

    if (a.kind === "post-materialized") {
      const unstaffed = members.filter((m) => !m.targetId);
      const pass = members.length > 0 && unstaffed.length === 0;
      return {
        id: a.id,
        kind: a.kind,
        pass,
        detail: pass
          ? `${members.length} post(s) staffed`
          : members.length === 0
            ? `no post matches '${a.target ?? "(any)"}'`
            : `unstaffed post(s): ${unstaffed.map((m) => m.title).join(", ")}`,
        evidence: evidenceOut,
      };
    }

    if (a.kind === "first-round-fired") {
      const fired = (evidence.turns ?? []).filter((t) => memberIds.has(t.memberId) && inWindow(t.at));
      evidenceOut.turnIds = fired.map((t) => t.turnId).filter(Boolean);
      evidenceOut.sourceRefs = fired.map((t) => `turn:${t.jobId ?? t.memberId}`);
      const pass = fired.length > 0;
      return {
        id: a.id,
        kind: a.kind,
        pass,
        detail: pass ? `first round fired for ${fired.length} post(s)` : `no round fired within the trial window for '${a.target ?? "(any)"}'`,
        evidence: evidenceOut,
      };
    }

    if (a.kind === "report-landed") {
      const landed = (evidence.reports ?? []).filter((r) => memberIds.has(r.memberId) && inWindow(r.createdAt));
      evidenceOut.reportIds = landed.map((r) => r.reportId).filter(Boolean);
      evidenceOut.turnIds = landed.map((r) => r.turnId).filter(Boolean);
      const pass = landed.length > 0;
      return {
        id: a.id,
        kind: a.kind,
        pass,
        detail: pass ? `${landed.length} report(s) landed` : `no report landed within the trial window for '${a.target ?? "(any)"}'`,
        evidence: evidenceOut,
      };
    }

    if (a.kind === "escalate-bounded") {
      const bound = Number.isFinite(a.expect) ? a.expect : 0;
      const count = Number(evidence.escalations ?? 0);
      const pass = count <= bound;
      return {
        id: a.id,
        kind: a.kind,
        pass,
        detail: `${count} open escalation(s), bound ${bound}`,
        evidence: evidenceOut,
      };
    }

    // 未知 kind（normalize 已报错；这里兜底判不绿，绝不静默通过）。
    return { id: a.id, kind: a.kind, pass: false, detail: `unsupported assertion kind '${a.kind}'`, evidence: evidenceOut };
  });
}
