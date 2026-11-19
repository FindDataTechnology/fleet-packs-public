#!/usr/bin/env python3
"""团队发布对象结构校验器（openspec: add-meta-fleet-line / fleet-team-release）。

提案单位不是 manifest，是「团队发布」八段（见 META-LINE.md §4）。本脚本只看**结构**，
不看内容好坏——内容好坏归试跑断言与评测舰队。

五条断言，各自能红：

  S1 八段齐且空段显式。没有内容必须写空值，**不许用省略表达「没有」**——省略与空值
     不可区分，正是「忘了」与「本来就没有」混成一团的来源。未知段名同样报错
     （拼错的段静默不生效，比一次响亮的拒绝更贵）。
  S2 acceptance 非空。空断言 = 无可验收之实，不得进入可售态（与试跑引擎
     「无断言清单不得判绿」同源）。
  S3 三支柱各至少一条断言，且每条支柱断言自带 `counterCase`（会让它判不绿的坏设计）。
     没有 counterCase 的断言无法证明自己能红——那只是「应该有」的散文。
  S4 升版必须携带上一版证据三项（回合健康 / 报数达成 / 评测偏离）；首版免。
  S5 断言 kind 必须在试跑引擎的闭集内、且 llm-judged 必须带 rubric、
     escalate-bounded 必须带有限 expect。
  S6 有排期的岗必须带 `cadence.promptTemplate`（平台建队 `validateFleetDraft` 强制，
     而包校验器不要求 ⇒「包装得上」不等于「队建得起来」）。
  S7（`--inventory`）存量发布对象回归：**已经写了的那些必须持续过 S1–S6**，
     且清单集合不得静默缩水。刻意不要求「每个 public+fleet 包都要有对象」——
     2026-11-15 实测 20 个 public fleet 包里只有 1 个有对象，按那个判据会得到
     一道常年红的闸；覆盖面只报数、不判红。

用法：
    python3 fleet-packs/scripts/check_release_object.py <release.json>
    python3 fleet-packs/scripts/check_release_object.py --inventory [--check <snapshot.json>] \\
        [--write-snapshot <snapshot.json>]
    python3 fleet-packs/scripts/check_release_object.py --self-test     # 5 臂：1 绿 + 4 红
退出码：0=通过；1=不通过（逐条指名）；2=用法错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SECTIONS = ["manifest", "org", "memory", "toolkit", "dashboard",
            "acceptance", "evidence", "cost"]

PILLARS = ["memory", "toolkit", "dashboard"]

# 与 paas/server/blueprint-validate.js 的 TRIAL_ASSERTION_KINDS 逐字同源。
# 引擎加新类型时这里要同步——但**不得**为了让校验通过而放宽闭集。
#
# 2026-11-17：三支柱结构断言（add-pillar-structural-assertions）进闭集。
# 同步**不再靠注释自觉**——`--self-test` 里有一臂直接读 paas 侧的数组字面量
# 逐项比对（`_engine_kinds()`），一侧改了另一侧没跟上就红。
ASSERTION_KINDS = ["post-materialized", "first-round-fired", "report-landed",
                   "escalate-bounded", "memory-charter-composed",
                   "toolface-narrowed", "console-declared", "llm-judged"]

#: 结构断言（机器判，不经模型、不需 owner 二次签字）。
STRUCTURAL_KINDS = frozenset({
    "memory-charter-composed", "toolface-narrowed", "console-declared",
})

#: 引擎侧闭集所在的文件（相对本脚本的仓库上两级 = 工作区根）。
_ENGINE_VALIDATE = Path(__file__).resolve().parents[2] / "paas" / "server" / "blueprint-validate.js"


def _engine_kinds() -> list[str] | None:
    """读 paas 的 `MACHINE_ASSERTION_KINDS` + `llm-judged`。

    取数组**字面量**而不是起 node 子进程：跨语言、只为一个常量数组起进程太重。
    代价是对格式敏感——所以取不到时返回 None 并让调用方报「取不到闭集」+ 文件
    路径 + 期望形状，**不静默跳过**（跳过等于把这道一致性闸关掉了）。
    """
    try:
        text = _ENGINE_VALIDATE.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"export const MACHINE_ASSERTION_KINDS = \[(.*?)\];", text, re.S)
    if not m:
        return None
    kinds = re.findall(r'"([^"]+)"', m.group(1))
    return kinds + ["llm-judged"] if kinds else None


def _is_empty(v) -> bool:
    return v is None or v == {} or v == [] or v == ""


def _version_is_first(manifest: dict) -> bool:
    """首版判定：manifest.version 缺省 / 0.x.y / 1.x.y 皆视为首版。

    「首版」的语义是**这一系列还没有上一版可比**，不是「主版本号恰好是 1」。
    0.x 是最典型的首版形态（新包一律 0.1.0 起步），把它判成非首版会要求它
    携带一个尚不存在的上一版证据——2026-11-17 实测踩到：投研评测舰首版
    0.1.0 被 `evidence_empty` 拒。

    按 semver 的主版本号判，**不要** `float()`——`float("1.0.0")` 抛 ValueError，
    会让 1.0.0 被误判成非首版（2026-11-13 自检首跑正是这么绿不起来的）。
    """
    v = str((manifest or {}).get("version") or "").strip().lstrip("v")
    if not v:
        return True
    m = re.match(r"^(\d+)", v)
    # 主版本号 ≤ 1 即首版：0.x 是 1.0 之前的预发布系列，同样没有「上一版」。
    return bool(m) and int(m.group(1)) <= 1


def validate(release) -> list[dict]:
    """返回错误清单（空 = 通过）。每条含 code / error / entry。"""
    errors: list[dict] = []

    def err(code: str, msg: str, entry: str) -> None:
        errors.append({"code": code, "error": msg, "entry": entry})

    if not isinstance(release, dict):
        err("not_an_object", "发布对象必须是 JSON 对象", "(root)")
        return errors

    # ── S1 八段齐 / 空段显式 / 未知段名 ───────────────────────────────────
    for name in SECTIONS:
        if name not in release:
            err("section_missing",
                f"缺少段 '{name}'——没有内容 MUST 写显式空值，不得省略", name)
    for name in sorted(set(release) - set(SECTIONS) - {"releaseId"}):
        err("section_unknown",
            f"未知段 '{name}'（已知：{', '.join(SECTIONS)}；另允许 releaseId）", name)

    # ── S2 acceptance 非空 ────────────────────────────────────────────────
    acceptance = release.get("acceptance")
    if "acceptance" in release and _is_empty(acceptance):
        err("acceptance_empty",
            "acceptance 为空——无可验收之实，不得进入可售态", "acceptance")

    assertions = acceptance if isinstance(acceptance, list) else []

    # ── S3 三支柱各至少一条，且带 counterCase ─────────────────────────────
    covered: dict[str, int] = {p: 0 for p in PILLARS}
    for i, a in enumerate(assertions):
        if not isinstance(a, dict):
            err("assertion_not_object", "断言必须是对象", f"acceptance[{i}]")
            continue
        pillar = a.get("pillar")
        if pillar in covered:
            covered[pillar] += 1
            cc = a.get("counterCase")
            if not isinstance(cc, str) or len(cc.strip()) < 8:
                err("counter_case_missing",
                    f"支柱断言 '{a.get('id')}' 缺 counterCase——没有它无法证明这条断言能红",
                    f"acceptance[{i}].counterCase")
    if not _is_empty(acceptance):
        for p in PILLARS:
            if covered[p] == 0:
                err("pillar_uncovered",
                    f"缺少支柱 '{p}' 的断言（三支柱各须至少一条能红的断言）",
                    "acceptance")

    # ── S5 kind 闭集 / rubric / expect ───────────────────────────────────
    for i, a in enumerate(assertions):
        if not isinstance(a, dict):
            continue
        kind = a.get("kind")
        if kind not in ASSERTION_KINDS:
            err("kind_unsupported",
                f"断言 '{a.get('id')}' 声明了不支持的 kind '{kind}'"
                f"（支持：{', '.join(ASSERTION_KINDS)}）", f"acceptance[{i}].kind")
            continue
        if kind == "llm-judged":
            rb = a.get("rubric")
            if not isinstance(rb, str) or len(rb.strip()) < 10:
                err("rubric_missing",
                    f"llm-judged 断言 '{a.get('id')}' 缺 rubric（≥10 字）",
                    f"acceptance[{i}].rubric")
        if kind == "escalate-bounded":
            exp = a.get("expect")
            if not isinstance(exp, (int, float)) or isinstance(exp, bool):
                err("expect_not_finite",
                    f"escalate-bounded 断言 '{a.get('id')}' 的 expect 必须是有限数",
                    f"acceptance[{i}].expect")

    # ── S4 升版必须带证据 ─────────────────────────────────────────────────
    manifest = release.get("manifest") if isinstance(release.get("manifest"), dict) else {}
    if not _version_is_first(manifest):
        ev = release.get("evidence")
        if not isinstance(ev, dict) or _is_empty(ev):
            err("evidence_empty",
                "非首版发布必须携带上一版证据（回合健康 / 报数达成 / 评测偏离）",
                "evidence")
        else:
            for key in ("turnHealth", "reportLanded", "evalDrift"):
                if key not in ev:
                    err("evidence_partial",
                        f"evidence 缺 '{key}'（升版证据三项必须齐）", f"evidence.{key}")

    # ── S6 编制能建起来：每个带 cadence 的岗必须有「这一轮做什么」 ─────────
    # 2026-11-14 实历：拿这份对象去平台自带干跑（`POST /api/fleet-architect/draft`，
    # 源码 `validateFleetDraft`，Nothing is persisted）时，三个岗**全都被拒**：
    # `members[i].cadence.promptTemplate is required when a cadence is set`。
    # 而包校验器（`paas/lib/pack-manifest.js`）**不要求** fleet.members 带 promptTemplate
    # ⇒ 离线四道闸全绿、一建队就三处报错。persona 讲「这个岗是谁、守什么」，promptTemplate
    # 讲「这一轮做什么」，两者不可互相替代：只有 persona 的岗装得上，但跑不起来。
    org = release.get("org") if isinstance(release.get("org"), dict) else {}
    for i, m in enumerate(org.get("members") or []):
        if not isinstance(m, dict):
            err("org_member_not_object", "org.members 的每一项必须是对象", f"org.members[{i}]")
            continue
        cad = m.get("cadence")
        if not cad:
            continue  # 无 cadence＝不排期（合法：被动岗）
        pt = cad.get("promptTemplate") if isinstance(cad, dict) else None
        if not isinstance(pt, str) or not pt.strip():
            err("org_member_prompt_missing",
                f"岗 '{m.get('ref') or m.get('title')}' 有排期"
                f"（{cad.get('cron') if isinstance(cad, dict) else '?'}）却没有 `cadence.promptTemplate`"
                "——平台建队强制要求它非空（`validateFleetDraft`：cadence.promptTemplate is required "
                "when a cadence is set），而包校验器不要求 ⇒「包装得上」不等于「队建得起来」",
                f"org.members[{i}].cadence.promptTemplate")

    return errors


def example_release() -> dict:
    """一份结构合规的样例。内容全是真实 schema 字段，不编造字段名。"""
    return {
        "releaseId": None,
        "manifest": {
            "name": "示例团队", "version": "1.0.0", "category": "fleet",
            "description": "结构样例，仅用于校验器自检",
            "skills": [{"name": "sample-skill", "description": "示例技能",
                        "content": "---\nname: sample-skill\ndescription: 示例技能\n---\n\n# 示例\n\n## 输出契约\n收尾必 report_status。\n"}],
            "agents": [{"id": "sample-a", "name": "样例岗",
                        "persona": "你是样例岗。收尾必 report_status。",
                        "resources": {"mcpTools": {"fd-info": ["locate_evidence"]}}}],
            "mcpServers": [{"registryName": "fd-info", "allowTools": ["locate_evidence"]}],
        },
        "org": {"name": "示例舰队", "members": [{"ref": "a", "title": "样例岗"}]},
        "memory": {"store": {"home": "member"}, "read": {"scope": "own+shared"},
                   "write": {"authority": "private"}, "process": {"distill": "on"},
                   "guidance": {"sample-a": "本岗只记报数摘要与失败归因，不记正文。"}},
        "toolkit": {"sample-a": {"fd-info": ["locate_evidence"]}},
        "dashboard": {"board": [], "posts": {}},
        "acceptance": [
            {"id": "mem-charter", "kind": "llm-judged", "pillar": "memory",
             "target": "样例岗",
             "rubric": "persona 尾段是否含【记忆章程】且与 manifest 的 memory.guidance 一致",
             "counterCase": "章程未组进 persona，或组进去了但与 manifest 不一致"},
            {"id": "toolface", "kind": "llm-judged", "pillar": "toolkit",
             "target": "样例岗",
             "rubric": "该岗运行时可见工具面是否 ⊆ 声明的 allowTools，且不含任何写工具",
             "counterCase": "运行时可见了声明之外的工具，或声明里混入了写工具"},
            {"id": "board-post", "kind": "llm-judged", "pillar": "dashboard",
             "target": "样例岗",
             "rubric": "该岗那一轮是否落下了可查的看板产物",
             "counterCase": "那一轮跑完了但没有任何看板产物落库"},
            {"id": "staffed", "kind": "post-materialized", "target": "样例岗"},
            {"id": "first-round", "kind": "first-round-fired", "target": "样例岗"},
        ],
        "evidence": {},
        "cost": {"turnsPerDay": 1, "tokensPerTurn": 60000, "note": "样例"},
    }


def inventory(root: Path) -> tuple[list[str], list[str]]:
    """返回 (有发布对象的包**相对路径**, 全部 public+fleet 包相对路径)。

    键用 manifest 的相对路径而不是目录名：2026-11-15 实测 `hotel/industry-watch-fleet`
    与 `flower/industry-watch-fleet` 同名 ⇒ 按目录名建键会让两个包互相顶掉，快照也跟着失真。
    对象位置仍按目录名取（`releases/<dir>/release.json`），同名包由打印让人看见。
    """
    have: list[str] = []
    total: list[str] = []
    for p in sorted(root.rglob("manifest.json")):
        rel = p.relative_to(root)
        if any(part.startswith(".") or part == "node_modules" for part in rel.parts):
            continue
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except Exception:     # noqa: BLE001 —— 读不动的包不属本闸（另有人管结构）
            continue
        if man.get("visibility") != "public" or man.get("category") != "fleet":
            continue
        key = rel.as_posix()
        total.append(key)
        if (root / "releases" / p.parent.name / "release.json").is_file():
            have.append(key)
    return sorted(have), sorted(total)


def run_inventory(root: Path, snapshot: str | None, write: str | None) -> int:
    """S7：已写的发布对象必须持续过 S1–S6；集合不得静默缩水。覆盖面只报数。"""
    have, total = inventory(root)
    if not total:
        print("✗ 判不出：一个 public+fleet 包都没扫到（根选错了？）", file=sys.stderr)
        return 2

    by_dir: dict[str, list[str]] = {}
    for key in total:
        by_dir.setdefault(Path(key).parent.name, []).append(key)
    dup = {d: v for d, v in by_dir.items() if len(v) > 1}

    print(f"发布对象覆盖：{len(have)}/{len(total)} 个 public+fleet 包有 release.json")
    for key in total:
        obj = root / "releases" / Path(key).parent.name / "release.json"
        mark = "✓" if obj.is_file() else "·"
        print(f"   {mark} {key}")
    for d, v in dup.items():
        print(f"   ⚠ 目录名 `{d}` 有 {len(v)} 个包（{v}）——对象按目录名取，两边会共用/顶掉同一份")

    bad = 0
    for key in have:
        path = root / "releases" / Path(key).parent.name / "release.json"
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {path.relative_to(root)} 读不动：{type(e).__name__}", file=sys.stderr)
            bad += 1
            continue
        errs = validate(obj)
        if errs:
            bad += 1
            print(f"❌ {path.relative_to(root)} 不通过（{len(errs)} 条）")
            for e in errs:
                print(f"   [{e['code']}] {e['entry']}: {e['error']}")
        else:
            print(f"✅ {path.relative_to(root)} 通过")

    if write:
        Path(write).write_text(json.dumps({"have": have}, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
        print(f"快照已写 {write}（{len(have)} 份）")

    shrunk = 0
    if snapshot:
        try:
            snap = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"✗ 判不出：快照 {snapshot} 读不动（{type(e).__name__}）", file=sys.stderr)
            return 2
        was = set(snap.get("have") or [])
        lost = sorted(was - set(have))
        if lost:
            shrunk = len(lost)
            print(f"❌ 发布对象集合比快照少了 {shrunk} 个：{lost}")
            print("   ——对象被删/改目录名，都会让一个原本可提审的包静默退出通道；"
                  "确属退役就把快照一起更新并写明理由")

    return 1 if (bad or shrunk) else 0


def self_test() -> int:
    """五臂：一份合规样例必须绿，四份坏样例必须各自红且指名。"""
    import copy
    import tempfile

    base = example_release()
    arms: list[tuple[str, dict, str]] = []

    ok = copy.deepcopy(base)
    arms.append(("合规样例必须绿", ok, ""))

    no_dash = copy.deepcopy(base)
    del no_dash["dashboard"]
    arms.append(("缺 dashboard 段必须红并指名", no_dash, "dashboard"))

    empty_acc = copy.deepcopy(base)
    empty_acc["acceptance"] = []
    arms.append(("空 acceptance 必须红", empty_acc, "acceptance 为空"))

    one_pillar = copy.deepcopy(base)
    one_pillar["acceptance"] = [a for a in one_pillar["acceptance"]
                                if a.get("pillar") != "dashboard"]
    arms.append(("缺 dashboard 支柱必须红并指名", one_pillar, "dashboard"))

    typo = copy.deepcopy(base)
    typo["memroy"] = {}
    arms.append(("未知段名必须红并指名", typo, "memroy"))

    # S6 两向：①有排期却缺 `promptTemplate` 必红（平台建队会拒，包校验器却放行——
    # 2026-11-14 用平台自带干跑 `fleet_draft` 才发现）；②无 cadence 的被动岗不得误判。
    no_prompt = copy.deepcopy(base)
    no_prompt["org"]["members"][0]["cadence"] = {"cron": "10 8 * * *", "tz": "Asia/Shanghai"}
    arms.append(("有排期却没有逐轮指令必须红", no_prompt, "cadence.promptTemplate"))

    passive = copy.deepcopy(base)
    passive["org"]["members"].append({"ref": "passive-post", "title": "被动岗",
                                      "target": {"kind": "persona", "id": "passive-post"}})
    arms.append(("无排期的被动岗不得误判", passive, ""))

    passed = 0
    with tempfile.TemporaryDirectory(prefix="fd-release-selftest-") as td:
        for i, (name, obj, must_mention) in enumerate(arms):
            p = Path(td) / f"arm{i}.json"
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            errs = validate(json.loads(p.read_text(encoding="utf-8")))
            if must_mention == "":
                good = not errs
                detail = "ok" if good else "；".join(e["error"] for e in errs)[:200]
            else:
                good = bool(errs) and any(must_mention in e["error"] or must_mention in e["entry"]
                                          for e in errs)
                detail = ("未指名 " + must_mention) if good is False else \
                         next((e["error"] for e in errs if must_mention in e["error"]
                               or must_mention in e["entry"]), "")
            print(f"  {'PASS' if good else 'FAIL'}  {name} — {detail}")
            passed += 1 if good else 0

    # ── S7 三臂（`--inventory`）──────────────────────────────────────────
    # 不能并进上面那轮：那轮只调 `validate()`，而 S7 判的是「存量对象整体」——
    # ① 已写的对象坏了要红；② 集合比快照缩水要红；③ 同快照要绿（否则「一出生就红」
    # 会让人分不清「闸写反」与「对象真坏」）。
    import tempfile

    def _mkroot(td: Path, tag: str, objs: dict[str, dict]) -> Path:
        root = td / tag
        (root / "releases").mkdir(parents=True)
        for name, obj in objs.items():
            pkg = root / "packs" / name
            pkg.mkdir(parents=True)
            (pkg / "manifest.json").write_text(
                json.dumps({"name": name, "visibility": "public", "category": "fleet"},
                           ensure_ascii=False), encoding="utf-8")
            rel = root / "releases" / name
            rel.mkdir()
            (rel / "release.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return root

    with tempfile.TemporaryDirectory(prefix="fd-rel-inv-") as td:
        td = Path(td)
        good = example_release()
        root = _mkroot(td, "root-ok", {"ok-pack": good})
        # ③ 同快照 → 绿
        snap = td / "snap.json"
        assert run_inventory(root, None, str(snap)) == 0
        same_ok = run_inventory(root, str(snap), None) == 0
        print(f"  {'PASS' if same_ok else 'FAIL'}  存量对象一致且同快照必须绿 — "
              f"{'绿' if same_ok else '把没变化的存量判红了'}")
        passed += 1 if same_ok else 0

        # ① 存量对象坏掉 → 红
        bad_obj = copy.deepcopy(good)
        del bad_obj["dashboard"]
        bad_root = _mkroot(td, "root-bad", {"bad-pack": bad_obj})
        broke_ok = run_inventory(bad_root, None, None) == 1
        print(f"  {'PASS' if broke_ok else 'FAIL'}  已写的对象缺段必须红 — "
              f"{'红' if broke_ok else '存量对象坏了却报绿'}")
        passed += 1 if broke_ok else 0

        # ② 集合比快照缩水 → 红（拿走对象文件即造出这种形状）
        (root / "releases" / "ok-pack" / "release.json").unlink()
        shrunk_ok = run_inventory(root, str(snap), None) == 1
        print(f"  {'PASS' if shrunk_ok else 'FAIL'}  对象被删（集合缩水）必须红 — "
              f"{'红' if shrunk_ok else '对象悄悄消失却报绿'}")
        passed += 1 if shrunk_ok else 0

    total_arms = len(arms) + 3
    print(f"self-test {'绿' if passed == total_arms else '红'} ({passed}/{total_arms})")

    # ── 闭集一致性（跨仓，add-pillar-structural-assertions）─────────────────
    # 此前这条只写在注释里（「逐字同源」），没有任何东西能发现分叉——而
    # 「声明支持、运行时落空」在观感上与全绿完全同形。
    engine = _engine_kinds()
    if engine is None:
        print(f"  FAIL  闭集一致性 — 取不到闭集：{_ENGINE_VALIDATE}"
              f"（期望该文件含 `export const MACHINE_ASSERTION_KINDS = [...]` 字面量）")
        return 1
    only_here = [k for k in ASSERTION_KINDS if k not in engine]
    only_engine = [k for k in engine if k not in ASSERTION_KINDS]
    if only_here or only_engine:
        detail = []
        if only_here:
            detail.append(f"仅本脚本有：{', '.join(only_here)}")
        if only_engine:
            detail.append(f"仅引擎有：{', '.join(only_engine)}")
        print(f"  FAIL  闭集一致性 — {'；'.join(detail)}")
        return 1
    print(f"  PASS  闭集一致性 — 两侧各 {len(engine)} 项逐项相等")
    return 0 if passed == total_arms else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="团队发布对象结构校验")
    ap.add_argument("path", nargs="?", help="发布对象 JSON 路径")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--example", action="store_true", help="打印一份合规样例")
    ap.add_argument("--inventory", action="store_true",
                    help="S7 存量盘点：已写的对象必须过校验、集合不得静默缩水（覆盖面只报数）")
    ap.add_argument("--check", dest="snapshot", help="与快照比集合（缩水即红）")
    ap.add_argument("--write-snapshot", help="把当前有个对象的包名写成快照")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.inventory:
        return run_inventory(REPO, args.snapshot, args.write_snapshot)
    if args.example:
        print(json.dumps(example_release(), ensure_ascii=False, indent=1))
        return 0
    if not args.path:
        ap.print_help()
        return 2

    try:
        release = json.loads(Path(args.path).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"✗ 读不到/解析不了 {args.path}：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    errs = validate(release)
    if not errs:
        print(f"✅ {args.path} 通过（八段齐 / 三支柱有断言 / kind 闭集 / 证据完整）")
        return 0
    print(f"❌ {args.path} 不通过（{len(errs)} 条）")
    for e in errs:
        print(f"   [{e['code']}] {e['entry']}: {e['error']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
