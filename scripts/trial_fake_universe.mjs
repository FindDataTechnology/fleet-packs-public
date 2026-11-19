#!/usr/bin/env node
// E3 假宇宙（openspec: add-meta-fleet-line / §6.4+§6.5）
//
// 目的：证三件事
//   ① 三份合成 brief 的「期望形状」可以组成一份断言集，且在**好证据**下判绿；
//   ② 每份 brief 的「坏设计清单」逐条注入后，**对应断言**判红（不是随便哪条红）；
//   ③ 全程零真市场、零真模型、零真 cell —— 判决用**钉住的 paas 裁决引擎**，
//      证据是手写的固定对象。
//
// 为什么引擎是钉住的那份、不是 import paas：
//   paas/server/blueprint-trial.js 的函数体是纯的，但文件顶部 import 了
//   cron/catalog/pack-store/provisioner/DB/llm-providers。`import` 一次就拉整棵
//   paas 运行图（还要 node_modules），于是本闸只在「有 paas 邻居的作者机」上绿，
//   干净 clone 直接 ERR_MODULE_NOT_FOUND —— 元线号称「可被任何人在任何地方验证」，
//   这一条当场就不成立。钉住的那份见 shared/meta-catalog/pinned/blueprint-trial-engine.mjs
//   （原样转录，出处与 blob 都写在文件头）。
//
// 钉住的风险由**漂移臂**兜住：
//   机器上只要能解析到 paas 检出（--paas-root / FP_PAAS_ROOT / 同级 paas/），
//   就把断言闭集、llm-judged 上限、以及本闸全部例子的 verdict（含 detail 文案）
//   在「钉的」与「线上的」两份引擎上各跑一遍逐条比。任何一处不同 → 判红并点名。
//   解析不到 paas 时，脚本明说「漂移未验」——不静默当绿，也不冒充已验。
//
// 用法：
//   node fleet-packs/scripts/trial_fake_universe.mjs                 # 钉的引擎；有 paas 就顺手比漂移
//   node fleet-packs/scripts/trial_fake_universe.mjs --paas-root DIR # 指定 paas 检出（必须齐，否则 exit 2）
// 退出码：0=全绿；1=有判错（含漂移）；2=--paas-root 指的目录不完整。

import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as engine from "../shared/meta-catalog/pinned/blueprint-trial-engine.mjs";

const { evaluateTrial, normalizeAcceptance, TRIAL_ASSERTION_KINDS } = engine;
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const SINCE = 1_000_000;

// ── 漂移臂的前置：定位 paas 检出 ────────────────────────────────────────────
const LIVE_FILES = ["server/blueprint-trial.js", "server/blueprint-validate.js"];

function liveRoot() {
  const at = process.argv.indexOf("--paas-root");
  const explicit = at >= 0 ? process.argv[at + 1] : process.env.FP_PAAS_ROOT;
  if (explicit) {
    const root = resolve(explicit);
    const missing = LIVE_FILES.filter((rel) => !existsSync(join(root, rel)));
    if (missing.length) {
      // 指了却指不全：明说并退出，不静默退化成「只跑钉的」。
      console.error(`--paas-root ${root} 缺文件（不静默跳过漂移臂）：${missing.join(", ")}`);
      process.exit(2);
    }
    return root;
  }
  const sibling = resolve(REPO, "..", "paas");
  return LIVE_FILES.every((rel) => existsSync(join(sibling, rel))) ? sibling : null;
}

let driftReason = "";
let live = null;
const root = liveRoot();
if (root) {
  try {
    live = {
      trial: await import(pathToFileURL(join(root, "server/blueprint-trial.js")).href),
      validate: await import(pathToFileURL(join(root, "server/blueprint-validate.js")).href),
    };
  } catch (err) {
    // 检出在、加载不了（多半是没 npm install）：这不怪 fleet-packs，点名降级。
    driftReason = `paas 检出 ${root} 加载失败：${String(err.message || err).split("\n")[0]}`;
  }
} else {
  driftReason = "未找到 paas 检出（给 --paas-root <dir> 或设 FP_PAAS_ROOT 即可比对）";
}

console.log(`引擎：pinned（转录自 paas@${engine.PROVENANCE.commit.slice(0, 9)}）`);
console.log(live ? `漂移：与 ${root} 逐例比对中` : `漂移：未验 —— ${driftReason}`);

// ── 假宇宙：三个成员 + 一轮回合 + 报数 ──────────────────────────────────
function goodEvidence() {
  const titles = ["监视岗", "扫描", "拆要素", "定级", "汇总", "盯梢岗", "分析岗", "投稿岗"];
  return {
    fleetId: "fleet_fake_0001",
    fleetName: "假宇宙舰队",
    members: titles.map((t, i) => ({ memberId: `m${i}`, title: t, targetId: `preset-${i}` })),
    turns: titles.map((_, i) => ({ turnId: `turn_${i}`, memberId: `m${i}`, jobId: `job_${i}`, at: SINCE + 60 })),
    reports: titles.map((_, i) => ({ reportId: `report_${i}`, memberId: `m${i}`, turnId: `turn_${i}`,
                                    createdAt: SINCE + 90, summary: "本轮完成" })),
    escalations: 1,
  };
}

// ── 三份 brief 对应的断言集（机器可判的子集）──────────────────────────────
const PLANS = [
  {
    brief: "single-post",
    title: "A 单岗节奏型",
    assertions: [
      { id: "a1", kind: "post-materialized", target: "监视岗" },
      { id: "a2", kind: "first-round-fired", target: "监视岗" },
      { id: "a3", kind: "report-landed", target: "监视岗" },
      { id: "a4", kind: "escalate-bounded", expect: 3 },
    ],
    bad: [
      { label: "去掉唯一岗位的 target（岗位未落位）", patch: (e) => {
          e.members.find((m) => m.title === "监视岗").targetId = null; }, expectRed: "a1" },
      { label: "让该岗不报数", patch: (e) => {
          e.reports = e.reports.filter((r) => r.memberId !== "m0"); }, expectRed: "a3" },
      { label: "该岗一轮都没点火", patch: (e) => {
          e.turns = e.turns.filter((t) => t.memberId !== "m0"); }, expectRed: "a2" },
      { label: "升级项超出上限", patch: (e) => { e.escalations = 9; }, expectRed: "a4" },
    ],
  },
  {
    brief: "chained-four",
    title: "B 四岗链式编导型",
    assertions: [
      { id: "b1", kind: "post-materialized", target: "扫描" },
      { id: "b2", kind: "post-materialized", target: "拆要素" },
      { id: "b3", kind: "post-materialized", target: "定级" },
      { id: "b4", kind: "post-materialized", target: "汇总" },
      { id: "b5", kind: "first-round-fired", target: "定级" },
      { id: "b6", kind: "report-landed", target: "定级" },
      { id: "b7", kind: "escalate-bounded", expect: 2 },
    ],
    bad: [
      { label: "定级岗没落位（链断）", patch: (e) => {
          e.members.find((m) => m.title === "定级").targetId = null; }, expectRed: "b3" },
      { label: "定级岗只跑不报", patch: (e) => {
          e.reports = e.reports.filter((r) => r.memberId !== "m3"); }, expectRed: "b6" },
      { label: "定级岗本轮未点火", patch: (e) => {
          e.turns = e.turns.filter((t) => t.memberId !== "m3"); }, expectRed: "b5" },
      { label: "升级无上限（expect=2 实际 9）", patch: (e) => { e.escalations = 9; }, expectRed: "b7" },
    ],
  },
  {
    brief: "three-pillar",
    title: "C 三支柱全开型",
    assertions: [
      { id: "c1", kind: "post-materialized", target: "盯梢岗" },
      { id: "c2", kind: "post-materialized", target: "分析岗" },
      { id: "c3", kind: "post-materialized", target: "投稿岗" },
      { id: "c4", kind: "report-landed", target: "盯梢岗" },
      { id: "c5", kind: "report-landed", target: "分析岗" },
      { id: "c6", kind: "report-landed", target: "投稿岗" },
      { id: "c7", kind: "escalate-bounded", expect: 3 },
    ],
    bad: [
      { label: "投稿岗没落位（板没人投）", patch: (e) => {
          e.members.find((m) => m.title === "投稿岗").targetId = null; }, expectRed: "c3" },
      { label: "分析岗不报数", patch: (e) => {
          e.reports = e.reports.filter((r) => r.memberId !== "m6"); }, expectRed: "c5" },
      { label: "盯梢岗不报数", patch: (e) => {
          e.reports = e.reports.filter((r) => r.memberId !== "m5"); }, expectRed: "c4" },
      { label: "升级项超上限", patch: (e) => { e.escalations = 9; }, expectRed: "c7" },
    ],
  },
];

let failures = 0;
let driftCases = 0;
const fail = (msg) => { failures += 1; console.log(`  FAIL  ${msg}`); };
const pass = (msg) => console.log(`  PASS  ${msg}`);

// 漂移臂：同一组 (assertions, evidence) 在线上引擎上再判一遍，逐字段比。
function judge(label, assertions, evidence) {
  const mine = evaluateTrial({ assertions, evidence, since: SINCE });
  if (!live) return mine;
  const theirs = live.trial.evaluateTrial({ assertions, evidence, since: SINCE });
  if (JSON.stringify(mine) === JSON.stringify(theirs)) { driftCases += 1; return mine; }
  fail(`漂移 · ${label}：钉的引擎与线上引擎判决不同`);
  for (let i = 0; i < Math.max(mine.length, theirs.length); i += 1) {
    const a = mine[i], b = theirs[i];
    if (JSON.stringify(a) !== JSON.stringify(b)) {
      console.log(`          ${a?.id ?? b?.id ?? "?"}: 钉的 pass=${a?.pass}「${a?.detail}」vs 线上 pass=${b?.pass}「${b?.detail}」`);
    }
  }
  return mine;
}

// 漂移臂之二：判据常量（闭集变了而钉的那份没跟上，例子可能一个都不覆盖到）。
if (live) {
  for (const [name, mine, theirs] of [
    ["MACHINE_ASSERTION_KINDS", engine.MACHINE_ASSERTION_KINDS, live.validate.MACHINE_ASSERTION_KINDS],
    ["TRIAL_ASSERTION_KINDS", TRIAL_ASSERTION_KINDS, live.validate.TRIAL_ASSERTION_KINDS],
    ["JUDGE_ASSERTION_CAP", engine.JUDGE_ASSERTION_CAP, live.validate.JUDGE_ASSERTION_CAP],
  ]) {
    if (JSON.stringify(mine) !== JSON.stringify(theirs)) {
      fail(`漂移 · ${name}：钉的 ${JSON.stringify(mine)} vs 线上 ${JSON.stringify(theirs)}`);
    } else {
      driftCases += 1;
    }
  }
}

// 断言类型必须在引擎闭集内（否则真机会以 unsupported kind 判不绿）
for (const plan of PLANS) {
  for (const a of plan.assertions) {
    if (!TRIAL_ASSERTION_KINDS.includes(a.kind)) {
      fail(`${plan.brief}: 断言 ${a.id} 的 kind '${a.kind}' 不在引擎闭集内`);
    }
  }
}

for (const plan of PLANS) {
  console.log(`\n== ${plan.title}（${plan.brief}）==`);

  // normalizeAcceptance 先过一遍（真机的第一道形状门）。注意它返回 { assertions, errors }，
  // **没有 ok 字段**——按 ok 判会永远误判。
  const norm = normalizeAcceptance(plan.assertions);
  if (live) {
    const theirs = live.validate.normalizeAcceptance(plan.assertions);
    if (JSON.stringify(norm) !== JSON.stringify(theirs)) {
      fail(`漂移 · ${plan.brief}：normalizeAcceptance 结果不同（钉的 ${JSON.stringify(norm)} vs 线上 ${JSON.stringify(theirs)}）`);
    } else {
      driftCases += 1;
    }
  }
  if ((norm.errors || []).length) {
    fail(`${plan.brief}: 断言集过不了 normalizeAcceptance：${JSON.stringify(norm.errors).slice(0, 200)}`);
    continue;
  }

  // ① 好证据 → 全绿
  const good = judge(`${plan.brief}/好证据`, norm.assertions, goodEvidence());
  const reds = good.filter((r) => !r.pass);
  if (reds.length) fail(`${plan.brief}: 好证据下有 ${reds.length} 条判红：${reds.map((r) => r.id + ':' + r.detail).join(' | ')}`);
  else pass(`${plan.brief}: 好证据 ${good.length} 条全绿`);

  // ② 坏设计逐条注入 → 指定断言判红
  for (const bad of plan.bad) {
    const ev = goodEvidence();
    bad.patch(ev);
    const out = judge(`${plan.brief}/${bad.label}`, norm.assertions, ev);
    const target = out.find((r) => r.id === bad.expectRed);
    if (!target) { fail(`${plan.brief}: 找不到断言 ${bad.expectRed}`); continue; }
    if (target.pass) fail(`${plan.brief} · ${bad.label} → 断言 ${bad.expectRed} 未判红（断言瞎了）`);
    else pass(`${plan.brief} · ${bad.label} → ${bad.expectRed} 判红（${target.detail.slice(0, 60)}）`);
  }
}

// ── D 判据边界臂 ──────────────────────────────────────────────────────────
// 为什么单独有这一段：坏设计注入全是「明显坏」（escalations=9 对上限 3），落在判据的
// 宽松区。实测把 `count <= bound` 改成 `count <= bound + 1`，只跑 A/B/C 时漂移臂
// 照样绿——语料没覆盖等号，漂移就不可观测。所以等号两侧必须自己占位：
// 成立要绿、多一格要红；窗口起点、空匹配、全未落位同理。
const BOUNDARIES = (() => {
  const ev = goodEvidence();
  // 把回合与报数整体挪到某个时刻，用于「窗口前 / 正好在起点」两种边界。
  const at = (ms) => ({
    ...ev,
    turns: ev.turns.map((t) => ({ ...t, at: ms })),
    reports: ev.reports.map((r) => ({ ...r, createdAt: ms })),
  });
  return [
    { label: "升级数正好等于上限（等号成立）", assertions: [{ id: "x1", kind: "escalate-bounded", expect: 3 }],
      evidence: { ...ev, escalations: 3 }, expect: { x1: true } },
    { label: "升级数 = 上限 + 1", assertions: [{ id: "x2", kind: "escalate-bounded", expect: 3 }],
      evidence: { ...ev, escalations: 4 }, expect: { x2: false } },
    { label: "未声明 expect（上限按 0）且无升级", assertions: [{ id: "x3", kind: "escalate-bounded" }],
      evidence: { ...ev, escalations: 0 }, expect: { x3: true } },
    { label: "未声明 expect（上限按 0）但有 1 条升级", assertions: [{ id: "x4", kind: "escalate-bounded" }],
      evidence: { ...ev, escalations: 1 }, expect: { x4: false } },
    { label: "岗位名一个都不匹配", assertions: [{ id: "x5", kind: "post-materialized", target: "不存在的岗" }],
      evidence: ev, expect: { x5: false } },
    { label: "岗位在但全都没落位（any 目标）", assertions: [{ id: "x6", kind: "post-materialized" }],
      evidence: { ...ev, members: ev.members.map((m) => ({ ...m, targetId: null })) }, expect: { x6: false } },
    { label: "回合在窗口之前", assertions: [{ id: "x7", kind: "first-round-fired", target: "监视岗" }],
      evidence: at(SINCE - 1), expect: { x7: false } },
    { label: "回合正好落在窗口起点（等号成立）", assertions: [{ id: "x8", kind: "first-round-fired", target: "监视岗" }],
      evidence: at(SINCE), expect: { x8: true } },
    { label: "报数在窗口之前", assertions: [{ id: "x9", kind: "report-landed", target: "监视岗" }],
      evidence: at(SINCE - 1), expect: { x9: false } },
  ];
})();

console.log(`\n== D 判据边界 ==`);
for (const b of BOUNDARIES) {
  const out = judge(`边界/${b.label}`, b.assertions, b.evidence);
  for (const [id, want] of Object.entries(b.expect)) {
    const got = out.find((r) => r.id === id);
    if (!got) { fail(`边界 · ${b.label}：找不到断言 ${id}`); continue; }
    if (got.pass !== want) {
      fail(`边界 · ${b.label} → ${id} 期望${want ? "绿" : "红"}、实得${got.pass ? "绿" : "红"}（${got.detail}）`);
    } else {
      pass(`边界 · ${b.label} → ${id} ${want ? "绿" : "红"}（${got.detail.slice(0, 50)}）`);
    }
  }
}

if (live) console.log(`\n漂移臂：${driftCases} 例与线上引擎判决一致`);
else console.log(`\n漂移臂：本机跳过（${driftReason}）`);

console.log(`\nfake-universe ${failures === 0 ? "绿" : `红（${failures} 处）`}`);
process.exit(failures === 0 ? 0 : 1);
