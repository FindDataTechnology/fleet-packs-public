#!/usr/bin/env python3
"""v2 垂直蓝图结构门（fleet-packs/blueprints/schema.md 的可失败版本）。

为什么需要这道闸：`blueprints/*/blueprint.json` 是**构造师的产物格式**，而此前没有任何东西
校验它——M7 只扫三份 `manifest.json`，E1' 查的是另一种八段发布对象（manifest/org/memory/
toolkit/dashboard/acceptance/evidence/cost），仓里没有一份文件是那个形状。于是「构造师交上来的
蓝图连段名写错都不会红」。2026-11-13 实测就有一处：`meta-grader` 的蓝图把 `anomaly_criteria`
写成 `criteria`、且没有 `notify`，而 schema.md 是契约真源——没人发现，因为没人读。

判据全部来自 schema.md（本仓的契约真源），不发明新规则：
  B1 顶层段：必填齐、未知段名拒、v1 形状必须显式登记为存档（否则判红）
  B2 boundary 三段非空
  B3 dataSources[].ref 字符集 + 必须命中目录（内部逻辑名要显式登记）
  B4 board[].artifact 四型闭集 + title/discipline 非空
  B5 monitoring.anomaly_criteria 非空 + escalate 在场（notify 缺＝告警，不判红）
  B6 acceptance：非空、id 唯一、kind 五型闭集、escalate 有数值 expect、llm-judged 有 rubric
  B7 fleet.members：title/kpiSlots/cadence.cron 齐 + 同舰队错峰 ≥30 分钟
  B8 内联物件下限（agents/skills/mcpServers 至少一项非空）+ mcpServers.registryName 命中目录

用法：python3 scripts/check_blueprint.py [--self-test]
退出码：0=全绿；1=有判错。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "shared" / "meta-catalog" / "catalog.json"

# 扫描范围：垂直蓝图在 blueprints/，共用包的蓝图在 shared/
GLOBS = ("blueprints/*/blueprint.json", "shared/*/blueprint.json")

# v1 存档：schema.md 明写「保持 v1 原样存档不转写——它是历史证据，不是模板」。
# 有了这张表，新增一份 v1 蓝图不会被静默放过：未登记就判红。
V1_ARCHIVES = {
    "blueprints/cross-border-ecom-policy/blueprint.json": "L3 首单试点真跑证据，schema.md 指定原样存档",
    "blueprints/sample-backfill/blueprint.json": "补数样例，schema.md 指定原样存档",
}

REQUIRED = ("boundary", "dataSources", "fleet", "board", "monitoring", "acceptance")
OPTIONAL = ("agents", "skills", "mcpServers", "i18n", "_casting", "tags", "notes", "kind", "releaseId")
BOARD_ARTIFACTS = ("doc", "dashboard", "form", "record-view")
ASSERTION_KINDS = ("post-materialized", "first-round-fired", "report-landed",
                   "escalate-bounded", "llm-judged")
MIN_GAP_MINUTES = 30
# schema.md：ref 是「在册 server 名或内部库逻辑名」。逻辑名必须显式登记，否则等于放行笔误。
INTERNAL_LOGIC_REFS: dict[str, str] = {}


def _expand_field(field: str, lo: int, hi: int) -> list[int] | None:
    """把 cron 的单个字段展开成取值列表。支持 `*` / `a` / `a,b` / `a-b` / `*/n` / `a-b/n`。

    返回 None 表示这个字段判不了（形态不认识）。**判不了不能算过**——调用方
    会把它报成「错峰判不了」，而不是放行。
    """
    field = str(field or "").strip()
    if not field:
        return None
    out: list[int] = []
    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            if not step_s.isdigit() or int(step_s) <= 0:
                return None
            step = int(step_s)
            part = base.strip()
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            return None
        if start < lo or end > hi or start > end:
            return None
        out.extend(range(start, end + 1, step))
    return sorted(set(out))


def _cron_fire_minutes(expr: str) -> list[int] | None:
    """返回该 cron 一天内的**全部**点火时刻（0..1439 的分钟数），去重升序。

    为什么不再只取「单个定点时刻」：2026-11-18 `blueprints/infra-factory` 被判红，
    它的 cron 是 `7,37 * * * *`（每逢 :07 与 :37）与 `17 9,21 * * *`（每天 9:17 与
    21:17）——都是**合法的 cron**，只是不是单值。旧实现只会 `int(parts[0])`，
    多值一律返回 None，于是把合法节奏判成「错峰判不了」。

    判据真正要回答的是「这两个 cron 会不会撞车」，那是可算的：展开所有点火时刻，
    取最小间隔。能力不够时判红是对的（「判不了不能算过」），但把**能判的**判成
    判不了就是假红——这道闸自己踩过一次（见上方 B7b 的注释：判错方向的代价是
    「合规编制被判红、按错结论去改包」）。

    限制（明说不做，避免被误当成全功能 cron 解析器）：不支持 `L`/`W`/`#`/`?`
    这类扩展语法，也不展开「月/周」维度的语义——错峰是**日内**的事，只要「每天
    是否都点」这一点在周/月维度上不确定，本函数仍按「每天都点」算，这是**偏保守**
    的方向（可能多判出碰撞，不会漏判）。
    """
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    minutes = _expand_field(parts[0], 0, 59)
    hours = _expand_field(parts[1], 0, 23)
    if minutes is None or hours is None:
        return None
    return sorted({h * 60 + m for h in hours for m in minutes})


def validate(doc, path: str, catalog: set[str]) -> tuple[list[str], list[str]]:
    """返回 (错误, 告警)。错误非空即判红。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(doc, dict):
        return ([f"{path}: 不是 JSON 对象"], [])

    # v1 存档判定：有 vertical/sources/boards 而无 boundary ⇒ v1
    looks_v1 = "vertical" in doc and "boundary" not in doc
    if looks_v1:
        if path in V1_ARCHIVES:
            return ([], [f"{path}: v1 存档（{V1_ARCHIVES[path]}），不按 v2 判"])
        return ([f"{path}: 这是 v1 形状（vertical/sources/boards）而未登记为存档——"
                 f"v1 平台已明确拒收，要留档请写进 V1_ARCHIVES 并说明理由"], [])

    # ── B1 顶层段 ──────────────────────────────────────────────────────────
    for name in REQUIRED:
        if name not in doc:
            errors.append(f"{path}: 缺少必填段 '{name}'（没有内容 MUST 写显式空值）")
    for name in sorted(set(doc) - set(REQUIRED) - set(OPTIONAL)):
        errors.append(f"{path}: 未知段 '{name}'（已知：{', '.join(REQUIRED + OPTIONAL)}）")

    # ── B2 boundary ───────────────────────────────────────────────────────
    bd = doc.get("boundary")
    if not isinstance(bd, dict):
        errors.append(f"{path}: boundary 必须是对象")
    else:
        for key in ("name", "oneLine", "outOfScope"):
            if not isinstance(bd.get(key), str) or not bd[key].strip():
                errors.append(f"{path}: boundary.{key} 必须是非空字符串（防编制蔓延）")

    # ── B3 dataSources ────────────────────────────────────────────────────
    ds = doc.get("dataSources")
    if not isinstance(ds, list) or not ds:
        errors.append(f"{path}: dataSources 必须是非空数组")
    else:
        for i, ent in enumerate(ds):
            ref = (ent or {}).get("ref") if isinstance(ent, dict) else None
            if not isinstance(ref, str) or not ref:
                errors.append(f"{path}: dataSources[{i}].ref 必须是非空字符串")
                continue
            if not all(ch.isalnum() or ch in "._-" for ch in ref) or not ref[0].isalnum() \
                    or ref != ref.lower():
                errors.append(f"{path}: dataSources[{i}].ref '{ref}' 不合字符约束"
                              f"（^[a-z0-9][a-z0-9._-]{{0,63}}$，禁端点/端口/凭据）")
            elif ref not in catalog and ref not in INTERNAL_LOGIC_REFS:
                errors.append(f"{path}: dataSources[{i}].ref '{ref}' 不在条线能力目录里，"
                              f"也不是已登记的内部逻辑名——按目录生成或改引用")

    # ── B4 board ──────────────────────────────────────────────────────────
    board = doc.get("board")
    if not isinstance(board, list) or not board:
        errors.append(f"{path}: board 必须是非空数组（产物骨架）")
    else:
        for i, ent in enumerate(board):
            if not isinstance(ent, dict):
                errors.append(f"{path}: board[{i}] 必须是对象")
                continue
            art = ent.get("artifact")
            if art not in BOARD_ARTIFACTS:
                errors.append(f"{path}: board[{i}].artifact '{art}' 不在闭集内"
                              f"（{'/'.join(BOARD_ARTIFACTS)}）")
            for key in ("title", "discipline"):
                if not isinstance(ent.get(key), str) or not ent[key].strip():
                    errors.append(f"{path}: board[{i}].{key} 必须是非空字符串")

    # ── B5 monitoring ─────────────────────────────────────────────────────
    mon = doc.get("monitoring")
    if not isinstance(mon, dict):
        errors.append(f"{path}: monitoring 必须是对象")
    else:
        crit = mon.get("anomaly_criteria")
        if not isinstance(crit, list) or not [c for c in crit if isinstance(c, str) and c.strip()]:
            errors.append(f"{path}: monitoring.anomaly_criteria 必须是非空字符串数组"
                          f"（写清「什么算异常」；实测过把一个岗位的判据写成空数组＝没有监控）")
        esc = mon.get("escalate")
        if not isinstance(esc, dict) or not esc:
            errors.append(f"{path}: monitoring.escalate 必须声明阈值与默认严重级（走拍板）")
        elif not isinstance(esc.get("threshold"), str) or not esc["threshold"].strip():
            errors.append(f"{path}: monitoring.escalate.threshold 必须是非空字符串")
        notify = mon.get("notify")
        if notify is None:
            warnings.append(f"{path}: monitoring 没声明 notify（schema 要求通知一律走 "
                            f"proposal-channel）——是缺口，不是通过")
        elif notify != "proposal-channel":
            errors.append(f"{path}: monitoring.notify '{notify}' 必须是 'proposal-channel'"
                          f"（通知不得直连外部通道）")

    # ── B6 acceptance ─────────────────────────────────────────────────────
    acc = doc.get("acceptance")
    if not isinstance(acc, list) or not acc:
        errors.append(f"{path}: acceptance 必须是非空数组——无可验收之实不得进入可售态")
    else:
        seen: dict[str, int] = {}
        for i, a in enumerate(acc):
            if not isinstance(a, dict):
                errors.append(f"{path}: acceptance[{i}] 必须是对象（v2 不收自由文本）")
                continue
            aid = a.get("id")
            if not isinstance(aid, str) or not aid.strip():
                errors.append(f"{path}: acceptance[{i}].id 必须是非空字符串")
            else:
                seen[aid] = seen.get(aid, 0) + 1
            kind = a.get("kind")
            if kind not in ASSERTION_KINDS:
                errors.append(f"{path}: acceptance[{i}] '{aid}' 的 kind '{kind}' 不在五型闭集内"
                              f"（{'/'.join(ASSERTION_KINDS)}；v1 自由文本平台已拒收）")
                continue
            if "target" in a and a["target"] is not None and not isinstance(a["target"], str):
                errors.append(f"{path}: acceptance[{i}] '{aid}' 的 target 必须是岗位标题字符串")
            if kind == "escalate-bounded":
                exp = a.get("expect")
                if not isinstance(exp, (int, float)) or isinstance(exp, bool):
                    errors.append(f"{path}: acceptance[{i}] '{aid}' 是 escalate-bounded 但 expect "
                                  f"不是有限数——没有上限就无法判「不超」")
            if kind == "llm-judged":
                rb = a.get("rubric")
                if not isinstance(rb, str) or len(rb.strip()) < 10:
                    errors.append(f"{path}: acceptance[{i}] '{aid}' 是 llm-judged 但 rubric "
                                  f"缺失或过短（空判据等于没有判据）")
        for aid, n in seen.items():
            if n > 1:
                errors.append(f"{path}: acceptance 里 id '{aid}' 重复 {n} 次——"
                              f"判决回包按 id 对位，重复会让两条互相覆盖")

    # ── B7 fleet ──────────────────────────────────────────────────────────
    fl = doc.get("fleet")
    if not isinstance(fl, dict):
        errors.append(f"{path}: fleet 必须是对象（与壹座集群模板 fleet 段同构）")
    else:
        members = fl.get("members")
        if not isinstance(members, list) or not members:
            errors.append(f"{path}: fleet.members 必须是非空数组")
        else:
            slots: list[tuple[str, int, int]] = []
            for i, m in enumerate(members):
                if not isinstance(m, dict):
                    errors.append(f"{path}: fleet.members[{i}] 必须是对象")
                    continue
                ref = m.get("ref") or m.get("title") or f"#{i}"
                if not isinstance(m.get("title"), str) or not m["title"].strip():
                    errors.append(f"{path}: fleet.members[{i}] '{ref}' 缺 title")
                if not isinstance(m.get("kpiSlots"), list) or not m["kpiSlots"]:
                    errors.append(f"{path}: fleet.members[{i}] '{ref}' 缺 kpiSlots"
                                  f"（报数键要与槽位逐字对齐）")
                cad = m.get("cadence") if isinstance(m.get("cadence"), dict) else {}
                fires = _cron_fire_minutes(cad.get("cron"))
                if fires is None:
                    errors.append(f"{path}: fleet.members[{i}] '{ref}' 的 cadence.cron "
                                  f"{cad.get('cron')!r} 形态不认识——错峰判不了就不能算过")
                else:
                    slots.append((ref, fires))
            # 错峰：同一舰队内任意两岗的**最小点火间隔** ≥30 分钟。
            # 逐对展开两边的全部点火时刻取最小差（多值 cron 也能判，见 _cron_fire_minutes）。
            for i in range(len(slots)):
                for j in range(i + 1, len(slots)):
                    a_ref, a_fires = slots[i]
                    b_ref, b_fires = slots[j]
                    gap = min(
                        min(abs(x - y), 1440 - abs(x - y))
                        for x in a_fires for y in b_fires
                    )
                    if gap < MIN_GAP_MINUTES:
                        errors.append(f"{path}: '{a_ref}' 与 '{b_ref}' 的最小点火间隔只有 {gap} 分钟 "
                                      f"< {MIN_GAP_MINUTES}——密集节奏会互踩 runtime")
        orch = fl.get("orchestration")
        if not isinstance(orch, dict) or not isinstance(orch.get("mode"), str) or not orch["mode"].strip():
            errors.append(f"{path}: fleet.orchestration.mode 必须声明（编排方式）")
        # B7b 编排边自洽：端点要在册、trigger 时刻要等于岗位节律。
        # 这里**故意不查「有 cron 就必须有入边」**：第一版这么写并把 meta-grader 的审计岗
        # 判红，照着去补边时被市场以 `auditor_not_orchestratable` 拒掉（审计官不进工作图）。
        # 回源码核对：`fleet-reconcile.js` 的 reconcileFleet 对每个有 cadence 的成员建定时
        # 任务，与编导边无关。判据写错方向的代价是「合规编制被判红、按错结论去改包」。
        if isinstance(members, list) and members:
            refs = {m.get("ref") for m in members if isinstance(m, dict) and m.get("ref")}
            cron_of = {m.get("ref"): ((m.get("cadence") or {}).get("cron")
                                      if isinstance(m.get("cadence"), dict) else None)
                       for m in members if isinstance(m, dict) and m.get("ref")}
            for e in (orch.get("edges") if isinstance(orch, dict) else None) or []:
                if not isinstance(e, dict):
                    continue
                to, frm = e.get("to"), e.get("from")
                for side, val in (("to", to), ("from", frm)):
                    if isinstance(val, str) and not val.startswith(("cron(", "event(", "webhook(")) \
                            and val not in refs:
                        errors.append(f"{path}: 编排边的 {side}={val!r} 不是本舰队成员"
                                      f"——拼错一个字这条边静默失效")
                if e.get("kind") == "trigger" and isinstance(frm, str) and frm.startswith("cron(") \
                        and isinstance(to, str) and to in cron_of:
                    inside = frm[5:-1].strip() if frm.endswith(")") else frm[5:]
                    if cron_of.get(to) and inside != cron_of.get(to):
                        errors.append(f"{path}: trigger cron({inside}) 与岗位 '{to}' 自己的 cron "
                                      f"{cron_of.get(to)!r} 不一致——两处节律不同时按哪个跑？")

    # ── B8 内联物件下限 + mcpServers ──────────────────────────────────────
    inline = [k for k in ("agents", "skills", "mcpServers", "bots")
              if isinstance(doc.get(k), list) and doc[k]]
    if not inline:
        errors.append(f"{path}: 没有任何内联物件（agents/skills/mcpServers）——"
                      f"铸造过不了发布门「至少一项非空」")
    mcps = doc.get("mcpServers")
    if isinstance(mcps, list):
        for i, ent in enumerate(mcps):
            if not isinstance(ent, dict):
                errors.append(f"{path}: mcpServers[{i}] 必须是对象")
                continue
            rn = ent.get("registryName")
            if not isinstance(rn, str) or not rn:
                errors.append(f"{path}: mcpServers[{i}].registryName 必须是非空字符串")
            elif rn not in catalog and rn not in INTERNAL_LOGIC_REFS:
                errors.append(f"{path}: mcpServers[{i}].registryName '{rn}' 不在条线能力目录里")
            tools = ent.get("allowTools")
            if not isinstance(tools, list) or not tools:
                errors.append(f"{path}: mcpServers[{i}] '{rn}' 的 allowTools 为空"
                              f"（要么列工具面，要么 fullSurface:true + 理由）")

    return errors, warnings


def catalog_servers() -> set[str]:
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    return set(cat.get("servers") or {})


def run() -> int:
    catalog = catalog_servers()
    if not catalog:
        print(f"✗ 条线能力目录读不到或为空：{CATALOG}——无法判 ref 是否在册", file=sys.stderr)
        return 1
    files = sorted({p for pat in GLOBS for p in REPO.glob(pat)})
    if not files:
        print("✗ 没找到任何蓝图文件", file=sys.stderr)
        return 1
    bad = 0
    for p in files:
        rel = str(p.relative_to(REPO))
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {rel}: 读不到/解析不了：{type(e).__name__}: {e}")
            bad += 1
            continue
        errs, warns = validate(doc, rel, catalog)
        for w in warns:
            print(f"  ⚠ {w}")
        for e in errs:
            print(f"  ✗ {e}")
            bad += 1
        if not errs:
            print(f"  ✅ {rel}")
    print(f"\nblueprint-gate {'绿' if bad == 0 else f'红（{bad} 处）'}")
    return 0 if bad == 0 else 1


def _base() -> dict:
    """一份最小合规 v2 蓝图（自检基线，不读磁盘上的产品文件）。"""
    return {
        "boundary": {"name": "样例舰队", "oneLine": "一句话用途", "outOfScope": "不做什么"},
        "dataSources": [{"ref": "wire-ops", "note": "样例"}],
        "board": [{"artifact": "doc", "title": "台账", "discipline": "draft+提案制"}],
        "monitoring": {"anomaly_criteria": ["连续两轮零报数"],
                       "escalate": {"threshold": "连续失败 ≥2 回合", "severity_default": "high"},
                       "notify": "proposal-channel"},
        "acceptance": [
            {"id": "a1", "kind": "post-materialized", "target": "岗位甲"},
            {"id": "a2", "kind": "escalate-bounded", "expect": 3},
            {"id": "a3", "kind": "llm-judged", "target": "岗位甲",
             "rubric": "日报合成且缺位如实标注「未收到」，无编造条目"},
        ],
        "fleet": {"name": "样例", "orchestration": {"mode": "chain", "edges": [
                      # 每个有 cron 的岗都要有入边（B7b）：基线必须自己先合规，否则
                      # 负向对照全被基线自身的红淹掉。
                      {"kind": "trigger", "from": "cron(7 9 * * *)", "to": "m1"},
                      {"kind": "trigger", "from": "cron(37 10 * * *)", "to": "m2"},
                  ]},
                  "members": [
                      {"ref": "m1", "title": "岗位甲", "kpiSlots": [{"key": "n"}],
                       "cadence": {"cron": "7 9 * * *"}},
                      {"ref": "m2", "title": "岗位乙", "kpiSlots": [{"key": "m"}],
                       "cadence": {"cron": "37 10 * * *"}},
                  ]},
        "mcpServers": [{"registryName": "wire-ops", "allowTools": ["read"]}],
    }


def self_test() -> int:
    """每条判据各一条坏样本：不证明能红，就不能说它在看。"""
    catalog = catalog_servers() or {"wire-ops"}
    base = _base()
    ok, _ = validate(copy.deepcopy(base), "base", catalog)
    if ok:
        print("FAIL  基线本身不绿——先修基线再谈负向对照")
        return 1

    arms: list[tuple[str, dict, str, str]] = []

    def arm(label: str, mutate, needle: str, path: str = "demo/blueprint.json") -> None:
        doc = copy.deepcopy(base)
        mutate(doc)
        arms.append((label, doc, needle, path))

    arm("缺必填段必须红", lambda d: d.pop("board"), "board")
    arm("未知段名必须红", lambda d: d.update(monitering={}), "未知段")
    arm("boundary 缺 outOfScope 必须红", lambda d: d["boundary"].pop("outOfScope"), "outOfScope")
    arm("ref 不合法字符必须红", lambda d: d["dataSources"].append({"ref": "http://x"}), "字符约束")
    arm("ref 不在目录必须红", lambda d: d["dataSources"].append({"ref": "ghost-mcp"}), "不在条线能力目录")
    arm("board.artifact 越界必须红", lambda d: d["board"].append(
        {"artifact": "pdf", "title": "x", "discipline": "y"}), "不在闭集")
    arm("monitoring 缺 anomaly_criteria 必须红", lambda d: d["monitoring"].pop("anomaly_criteria"),
        "anomaly_criteria")
    arm("monitoring.notify 直连外部必须红", lambda d: d["monitoring"].update(notify="email"),
        "proposal-channel")
    arm("kind 越界必须红", lambda d: d["acceptance"].append({"id": "a9", "kind": "free-text"}),
        "五型闭集")
    arm("escalate 缺数值 expect 必须红", lambda d: d["acceptance"].append(
        {"id": "a8", "kind": "escalate-bounded", "expect": "三"}), "expect")
    arm("llm-judged 缺 rubric 必须红", lambda d: d["acceptance"].append(
        {"id": "a7", "kind": "llm-judged"}), "rubric")
    arm("acceptance id 重复必须红", lambda d: d["acceptance"].append(
        {"id": "a1", "kind": "post-materialized"}), "重复")
    arm("错峰不足 30 分钟必须红", lambda d: d["fleet"]["members"][1]["cadence"].update(
        cron="17 9 * * *"), "互踩 runtime")
    arm("cron 判不了必须红", lambda d: d["fleet"]["members"][0]["cadence"].update(
        cron="@daily"), "错峰判不了")
    arm("没有内联物件必须红", lambda d: [d.pop(k, None) for k in ("agents", "skills", "mcpServers")],
        "内联物件")
    arm("mcpServers.registryName 不在目录必须红", lambda d: d["mcpServers"].append(
        {"registryName": "nope-mcp", "allowTools": ["x"]}), "不在条线能力目录")
    # B7b 两条：端点拼错、trigger 与岗位节律不一致。（「有 cron 就必须有入边」这条
    # 判据被实测证伪，见 validate() 里的注释——审计官不进工作图、普通岗按 cadence 建任务。）
    arm("编排边 ref 拼错必须红", lambda d: d["fleet"]["orchestration"]["edges"].append(
        {"kind": "handoff", "from": "m1", "to": "m22"}), "不是本舰队成员")
    arm("trigger 的 cron 与岗位节律不一致必须红", lambda d: d["fleet"]["orchestration"].update(
        edges=[{"kind": "trigger", "from": "cron(37 10 * * *)", "to": "m1"},
               {"kind": "trigger", "from": "cron(37 10 * * *)", "to": "m2"}]),
        "两处节律不同")

    passed = 0
    for label, doc, needle, path in arms:
        errs, _warns = validate(doc, path, catalog)
        hit = any(needle in e for e in errs)
        print(f"  {'PASS' if hit else 'FAIL'}  {label} — "
              f"{next((e for e in errs if needle in e), '未指名 ' + needle)[:120]}")
        passed += 1 if hit else 0

    # 反向臂：v1 存档登记了就放过，没登记就判红——两条都要能判
    v1 = {"vertical": {"name": "x"}, "sources": [], "boards": [], "fleet": {}, "acceptance": []}
    e_unreg, _ = validate(copy.deepcopy(v1), "new/v1-blueprint.json", catalog)
    unreg_ok = any("未登记为存档" in e for e in e_unreg)
    archived = next(iter(V1_ARCHIVES))
    e_reg, w_reg = validate(copy.deepcopy(v1), archived, catalog)
    reg_ok = not e_reg and any("v1 存档" in w for w in w_reg)
    for label, good, detail in (("未登记的 v1 必须红", unreg_ok, str(e_unreg)[:100]),
                                ("已登记的 v1 存档必须放过", reg_ok, str(e_reg or w_reg)[:100])):
        print(f"  {'PASS' if good else 'FAIL'}  {label} — {detail}")
        passed += 1 if good else 0

    total = len(arms) + 2
    print(f"self-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v2 垂直蓝图结构门")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    return self_test() if args.self_test else run()


if __name__ == "__main__":
    sys.exit(main())
