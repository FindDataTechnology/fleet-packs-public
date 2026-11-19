#!/usr/bin/env python3
"""ERP 团队覆盖闸：21 格矩阵 ↔ 团队编制 ↔ 工具面（跨仓，只读，不写盘）。

change: establish-erp-kernel-toolkit-and-teams 2.3 / 4.x / 5.1。

它回答的问题是「矩阵里每一格由谁做，而且这个人真的做得了」——不是「写了几个团队」。
十三族判据，任何一族不过即红：

  ① 矩阵在位且可解析（缺了就红；没有输入时全绿的闸比没有闸更糟）
  ② 每个可用格（delivered / template_configured）有承担者（fleet 或 template）
  ③ fleet 承担者必须落到一个真实团队目录（erp/<ref>/），且该团队有 coverage.json
  ④ 团队声明的覆盖格 ⊆ 矩阵 module_set（不许新造格子）
  ⑤ 一个格不许被两个团队声明；被 fleet 承担的格必须恰好被一个团队声明
  ⑤b template.json 的 coverage.modules 必须与 coverage.json 逐项相等（一份真源）
  ⑥ none 承担者只允许出现在 needs_onboarding / not_doing 格
  ⑦ 每个团队三岗齐（watcher / reconciler / decider）
  ⑧ 逐岗工具面必须存在（不许只在包级声明一次全队共享）
  ⑨ 逐岗工具必须存在于工具面（改名/下线会让授权静默失效）
  ⑩ 确认门类工具只许出现在拍板岗
  ⑪ 写类工具不许出现在巡检岗（巡检的产出是报告，不是事实）
  ⑫ 团队成员的 presetId 必须在角色包里真实存在；cron 不许撞车；验收条目 ≥4

覆盖声明放在 `erp/<team>/coverage.json`（旁挂文件，不入 pack manifest，避免碰 pack schema）。
团队声明的模块是**人写的事实**，闸只核对它与矩阵/工具面是否自洽——不替它生成。

用法：
  python3 fleet-packs/scripts/check_erp_team_coverage.py
  python3 fleet-packs/scripts/check_erp_team_coverage.py --json
退出码：0 一致 / 1 有违规 / 2 出处缺失
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent          # fleet-packs
FINDDATA = REPO.parent
ERP = REPO / "erp"
MATRIX = FINDDATA / "fd-erp" / "shared" / "coverage-matrix.json"
TOOL_SURFACE = FINDDATA / "fd-erp-ledger-mcp" / "tool-surface.json"

#: 可用态：必须有承担者
AVAILABLE = ("delivered", "template_configured")
#: 无承担者合法态
NO_OWNER_OK = ("needs_onboarding", "not_doing")
#: 确认门类工具：只许拍板岗持有
GATE_TOOLS = frozenset({
    "confirm_proposal", "decide_event",
    "approve_billing_rule", "approve_posting_rule",
})
#: 写类工具：巡检岗不许持有
WRITE_TOOLS = frozenset({
    "submit_proposal", "ingest_event", "submit_event", "project_event",
    "post_event", "reserve_credit", "release_credit", "reverse_fee",
    "submit_billing_rule", "submit_posting_rule",
})
ROLE_SUFFIXES = ("-watcher", "-reconciler", "-decider")
REGISTRY = "fd-erp-ledger-mcp"
#: 只读工具：门岗可以顺带持（读不能造成后果）
READONLY_TOOLS = frozenset({
    "query_events", "query_balance", "query_entries", "query_object", "query_metrics",
    "list_proposals", "list_billing_rules", "list_posting_rules", "list_posting_gaps",
    "run_reconciliation", "describe_event_type", "list_event_types",
})


def _load(path: Path, what: str, problems: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        problems.append(f"[出处缺失] {what} 不在位：{path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"[出处不可解析] {what}：{path} — {exc}")
        return None


def _role_of(agent_id: str) -> str | None:
    for suffix in ROLE_SUFFIXES:
        if agent_id.endswith(suffix):
            return suffix.lstrip("-")
    return None


def _mcp_tools(agent: dict[str, Any]) -> list[str] | None:
    tools = ((agent.get("resources") or {}).get("mcpTools") or {}).get(REGISTRY)
    return list(tools) if tools is not None else None


def _kernel_tools(agent: dict[str, Any]) -> tuple[bool, set[str]]:
    """(是否逐岗声明过 mcpTools, 本岗实际持有的内核工具集)。

    与 `_mcp_tools` 的区别在这条边界：`mcpTools` 键根本不存在 = 没做逐岗收窄（红）；
    `mcpTools` 存在但没有本 registry 键 = **显式声明本岗不拿任何内核工具**（合法，
    典型是采集岗/发布草稿岗）。这两件事被同一个 None 混在一起过。
    """
    resources = agent.get("resources") or {}
    if "mcpTools" not in resources:
        return False, set()
    per_registry = resources.get("mcpTools") or {}
    return True, set(per_registry.get(REGISTRY) or [])


def check(matrix_path: Path = MATRIX, surface_path: Path = TOOL_SURFACE,
          erp_dir: Path = ERP) -> dict[str, Any]:
    problems: list[str] = []
    matrix = _load(matrix_path, "覆盖矩阵", problems)
    surface = _load(surface_path, "内核工具面", problems)
    if matrix is None or surface is None:
        # 出处缺失时也要返回完整形状：判据集为空不等于「没问题」，调用方（含 main 的
        # 打印与 --json 的消费者）必须能看到「没跑成」而不是一个空壳。
        return {"ok": False, "problems": problems, "cells": 0, "teams": 0,
                "covered": [], "teams_declared": {}, "cron_slots": {}}

    tools = set(surface.get("tool_annotations") or {})
    cells = matrix.get("modules") or []
    module_set = set(matrix.get("module_set") or [c["id"] for c in cells])
    state_of = {c["id"]: c["state"] for c in cells}
    owner_of = {c["id"]: (c.get("owner") or {}) for c in cells}

    # ---- ②⑥ 承担者合法性（从团队视角再核一遍，不只信 fd-erp 侧闸）----
    for cid, state in state_of.items():
        owner = owner_of[cid]
        kind = owner.get("kind")
        if state in AVAILABLE:
            if kind not in ("fleet", "template"):
                problems.append(
                    f"[无承担者] {cid}（{state}）承担者应为 fleet/template，实为 {kind!r}")
        elif state in NO_OWNER_OK:
            if kind != "none":
                problems.append(
                    f"[承担者越权] {cid}（{state}）不是可用态，承担者却写了 {kind!r}"
                    "——需接入/不做的格子不该有团队")
    # ---- ③ fleet 承担者必须落地成团队 ----
    teams: dict[str, dict[str, Any]] = {}
    for team_dir in sorted(p for p in erp_dir.iterdir() if p.is_dir()):
        tpl = team_dir / "template.json"
        if not tpl.is_file():
            continue
        body = _load(tpl, f"{team_dir.name}/template.json", problems)
        if body is None or not body.get("fleet"):
            continue  # 岗位包（无 fleet 段）不是团队
        teams[team_dir.name] = {"dir": team_dir, "template": body}

    fleet_owners = sorted({o.get("ref") for o in owner_of.values()
                           if o.get("kind") == "fleet"})
    for ref in fleet_owners:
        if ref not in teams:
            problems.append(
                f"[团队悬空] 矩阵有格由团队 {ref!r} 承担，erp/{ref}/template.json 不存在")

    # ---- ④⑤ 团队覆盖声明 ↔ 矩阵 ----
    claimed_by: dict[str, list[str]] = defaultdict(list)
    declared: dict[str, list[str]] = {}
    meta_teams: set[str] = set()
    for name, info in teams.items():
        side = info["dir"] / "coverage.json"
        # 元团队（造团队的团队，如 erp-team-forge）不认领任何矩阵格：production 是
        # 团队本身，不是账务作业。声明 `"meta": true` 才免，且只免「认领矩阵格」这一件；
        # 编制/收窄/节奏/验收判据照跑。未声明 meta 的团队仍然必须有 coverage.json。
        if not side.is_file():
            problems.append(
                f"[覆盖声明缺失] {name}/coverage.json 不在位——团队必须声明它承担哪些格")
            continue
        body = _load(side, f"{name}/coverage.json", problems)
        if body is None:
            continue
        if body.get("meta") is True:
            meta_teams.add(name)
            declared[name] = []
            continue
        mods = list(body.get("modules") or [])
        declared[name] = mods
        for m in mods:
            if m not in module_set:
                problems.append(f"[新造格] {name} 声明了矩阵里不存在的格 {m!r}")
        # ⑤b 同一件事不许有第二份真源：`template.json` 里的 coverage.modules 随 pack
        # 分发、`coverage.json` 才是矩阵核对面。两份并存本身没错，错的是没人校它们
        # 相等——11-12 实测就出现过：coverage.json 收窄了 invoice 格，template.json
        # 还写着，闸全绿而面板与矩阵各说一套。这里把这一对钉死。
        # **未声明不算违规**：既有设计就是把覆盖声明放在旁挂的 coverage.json、不碰
        # pack schema（见 coverage.json 的文件头），所以 template.json 可以整段没有；
        # 一旦写了就必须与真源逐项相等。
        tpl_mods = list(((info["template"].get("coverage") or {}).get("modules")) or [])
        if tpl_mods and set(tpl_mods) != set(mods):
            only_tpl = sorted(set(tpl_mods) - set(mods))
            only_side = sorted(set(mods) - set(tpl_mods))
            problems.append(
                f"[两份覆盖声明不一致] {name}：template.json 的 coverage.modules 与 "
                f"coverage.json 不相等（只在 template.json：{only_tpl or '—'}；"
                f"只在 coverage.json：{only_side or '—'}）——真源是 coverage.json，"
                f"另一个必须跟着改")
            claimed_by[m].append(name)
    for m, names in sorted(claimed_by.items()):
        if len(names) > 1:
            problems.append(f"[重复承担] 格 {m} 被多个团队声明：{', '.join(names)}")
        if m in module_set:
            owner = owner_of.get(m) or {}
            for nm in names:
                if owner.get("kind") != "fleet" or owner.get("ref") != nm:
                    # 反方向：团队认领了矩阵没交给它的格（典型是认领了一个「明确不做」
                    # 或「需一次接入」的格）。这比漏声明更危险——它会让面板上出现一个
                    # 正在执行我们决定不做的事情的团队。
                    problems.append(
                        f"[越权认领] {nm} 声明承担 {m}，但矩阵把该格交给 "
                        f"{owner.get('kind')}:{owner.get('ref') or owner.get('reason') or '—'}"
                        "——团队不能自己给自己加格子")
    for m, owner in owner_of.items():
        if owner.get("kind") != "fleet":
            continue
        ref = owner.get("ref")
        if m in module_set and ref in teams and m not in (declared.get(ref) or []):
            problems.append(
                f"[声明缺漏] 矩阵把 {m} 交给 {ref}，但 {ref}/coverage.json 没声明它")

        # ---- ⑦⑧⑨⑩⑪ 团队编制与工具面 ----
    crons: dict[str, list[str]] = defaultdict(list)
    for name, info in teams.items():
        tpl = info["template"]
        agents = tpl.get("agents") or []
        # 「本域没有确认门」的声明在 coverage.json（与模块清单同文件，避免再开一份配置）
        side_note: dict[str, Any] = {}
        side = erp_dir / name / "coverage.json"
        if side.is_file():
            try:
                side_note = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                side_note = {}
        roles = {_role_of(a.get("id", "")): a for a in agents}
        for need in ("watcher", "reconciler", "decider"):
            if need not in roles:
                problems.append(f"[三岗不全] {name} 缺 {need} 岗")
        # ⑧⑨⑩⑪ 判的是**内核**工具面（确认门/写面/工具名在册）。不绑内核的团队
        # （元团队绑 fleet-architect + facet-market-mcp，），这几条对它没有判据——
        # 拿内核工具面去核一个不调内核的岗位，只会得出「工具悬空」的假红。
        # 编制（⑦）与节奏/槽位/验收（⑫）照跑，它们与工具面无关。
        binds_kernel = any(
            s.get("registryName") == REGISTRY for s in (tpl.get("mcpServers") or []))
        for role, agent in (roles.items() if binds_kernel else []):
            got = _mcp_tools(agent)
            if got is None:
                problems.append(
                    f"[未逐岗收窄] {name}/{role} 没有 resources.mcpTools——"
                    "包级全队共享等于把拍板岗的确认门发给全队")
                continue
            for t in got:
                if t not in tools:
                    problems.append(f"[工具悬空] {name}/{role} 授权了工具面里没有的 {t!r}")
            if role == "decider":
                # 拍板岗要么持确认门，要么在 coverage.json 里声明「本域没有确认门」并
                # 说清它凭什么有拍板权。信贷额度就是这样：内核的占用/释放是直接记账，
                # 没有 proposal/事件确认门——这不是团队的缺陷，是内核面的实情，
                # 但必须写出来，否则「拍板岗」三个字会被读成「有人把关」。
                if not (GATE_TOOLS & set(got)):
                    reason = str(side_note.get("no_gate_reason") or "").strip()
                    if not reason:
                        problems.append(
                            f"[确认门缺失] {name}/decider 既没有确认门工具，也没在 "
                            "coverage.json 的 no_gate_reason 里说明本域为何无门——"
                            "拍板岗不能是个空名")
                    elif not (set(got) & WRITE_TOOLS):
                        problems.append(
                            f"[拍板无实权] {name}/decider 声明本域无确认门（{reason[:40]}…），"
                            "但也没持有任何写工具——那它拍板拍的是什么？")
            elif GATE_TOOLS & set(got):
                problems.append(
                    f"[确认门越权] {name}/{role} 持有确认门工具 "
                    f"{sorted(GATE_TOOLS & set(got))}——确认门只许拍板岗")
            if role == "watcher":
                bad = sorted(WRITE_TOOLS & set(got))
                if bad:
                    problems.append(
                        f"[巡检越权] {name}/watcher 持有写工具 {bad}——"
                        "巡检的产出必须是报告，不是事实")

        # ---- ⑫ preset 可解析 / cron 不撞 / 验收条目 ----
        members = (tpl.get("fleet") or {}).get("members") or []
        for m in members:
            cadence = m.get("cadence") or {}
            cron = (cadence.get("cron") or "").strip()
            if cron:
                crons[cron].append(f"{name}/{m.get('ref')}")
            if not (m.get("kpiSlots") or []):
                problems.append(f"[无 KPI] {name}/{m.get('ref')} 没有 kpiSlots——空转无法被看见")
        # acceptance / monitoring 在**顶层**（既有已上架团队就是这样，paas 铸造读顶层）；
        # 同时容忍写在 fleet 段里的写法，免得换形状就判红。
        fleet = tpl.get("fleet") or {}
        acceptance = tpl.get("acceptance") or fleet.get("acceptance") or []
        if len(acceptance) < 4:
            problems.append(f"[验收不足] {name} 验收条目 {len(acceptance)} 条（要求 ≥4）")
        if not (tpl.get("monitoring") or fleet.get("monitoring")):
            problems.append(f"[异常判据缺失] {name} 没有 monitoring 段")
        # 团队控制台声明（pack-console）：装上之后团队没有脸，等于白装。
        # 这里只判「声明在不在、块是不是非空」——块 id 与参数白名单归 paas 的
        # lib/console-blocks-spec.js 判（第二份白名单必然漂移）。
        console = tpl.get("console")
        if not isinstance(console, dict) or not (console.get("blocks") or []):
            problems.append(
                f"[看板声明缺失] {name} 没有 console 段（或 blocks 为空）——"
                "团队装上后没有团队控制台；块 id 与参数由 paas 校验器判")

        # presetId 必须能在对应角色包里找到同名 agent
        for m in members:
            preset = ((m.get("target") or {}).get("presetId") or "")
            roles_pack = erp_dir / f"{name}-roles"
            roles_tpl = roles_pack / "template.json"
            if not preset or not roles_tpl.is_file():
                continue
            body = _load(roles_tpl, f"{name}-roles/template.json", problems)
            if body is None:
                continue
            ids = {a.get("id") for a in (body.get("agents") or [])}
            if preset not in ids:
                problems.append(
                    f"[preset 悬空] {name} 的成员 {m.get('ref')} 指向 presetId "
                    f"{preset!r}，但 {name}-roles 里没有这个岗位")

    for cron, who in sorted(crons.items()):
        if len(who) > 1:
            problems.append(f"[cron 撞车] {cron} 被多个成员共用：{', '.join(who)}")

    covered = sorted({m for mods in declared.values() for m in mods})
    available = sorted(m for m, s in state_of.items() if s in AVAILABLE)
    missing = [m for m in available if m not in covered
               and owner_of[m].get("kind") != "template"]
    for m in missing:
        problems.append(f"[悬空格] {m} 是可用态但没有团队承担（也没有登记模板承担）")

    return {
        "ok": not problems,
        "problems": problems,
        "cells": len(cells),
        "teams": len(teams),
        "covered": covered,
        "teams_declared": declared,
        "cron_slots": {k: v for k, v in sorted(crons.items())},
    }


def check_governance(teams_root: Path, surface_path: Path = TOOL_SURFACE,
                     registry: str = REGISTRY,
                     exempt: set[str] | None = None) -> dict[str, Any]:
    """跨板块：绑了内核的团队必须显式声明「谁持门、谁只读」（change 3.3）。

    与 check() 的区别：check() 管 ERP 棋盘团队（三岗 + 矩阵格），本函数管**其他板块**
    绑了 `fd-erp-ledger-mcp` 的团队。判据不靠岗位名猜（`web-reviewer` 里的 review 是
    人起的名字，不是契约），而靠模板里的 `_governance` 声明：

      · gate_roles      持确认门（decide_event / confirm_proposal / approve_*_rule）
      · draft_roles     只许写 draft 车道（submit_event / submit_proposal）
      · readonly_roles  一个写工具都不许有

    三条硬判据：声明覆盖全部岗位；门工具只在 gate_roles；写工具不出现在 readonly_roles。

    `exempt` 是已由 check() 用更严判据（岗位后缀 -watcher/-reconciler/-decider 固定
    语义）管住的团队名，不要求再写一份 `_governance`——两份声明必然漂移，而漂移时
    没人知道该信哪份。
    """
    problems: list[str] = []
    surface = _load(surface_path, "内核工具面", problems)
    if surface is None:
        return {"ok": False, "problems": problems, "teams": 0, "declared": {}}
    tools = set(surface.get("tool_annotations") or {})
    exempt = exempt or set()

    teams = 0
    declared: dict[str, dict[str, list[str]]] = {}
    for tpl_path in sorted(teams_root.glob("*/template.json")):
        name = tpl_path.parent.name
        if name in exempt:
            continue
        body = _load(tpl_path, f"{name}/template.json", problems)
        if body is None:
            continue
        servers = body.get("mcpServers") or []
        if not any(s.get("registryName") == registry for s in servers):
            continue  # 没绑内核的团队不归本闸管
        teams += 1
        gov = body.get("_governance") or {}
        if not gov:
            problems.append(
                f"[治理声明缺失] {name} 绑了 {registry} 却没写 _governance"
                "——谁持确认门、谁只读必须显式声明，不能靠岗位名猜")
            continue
        gate_roles = list(gov.get("gate_roles") or [])
        draft_roles = list(gov.get("draft_roles") or [])
        readonly_roles = list(gov.get("readonly_roles") or [])
        declared[name] = {"gate": gate_roles, "draft": draft_roles,
                          "readonly": readonly_roles}

        agents = {a.get("id"): a for a in (body.get("agents") or [])}
        covered = set(gate_roles) | set(draft_roles) | set(readonly_roles)
        for aid in agents:
            if aid not in covered:
                problems.append(
                    f"[岗位未归类] {name}/{aid} 没出现在 _governance 的任何角色清单里"
                    "——未归类的岗位等于权限边界没定义")
        for rid in sorted(covered - set(agents)):
            problems.append(f"[角色悬空] {name} 的 _governance 引用了不存在的岗位 {rid!r}")
        overlap = ((set(gate_roles) & set(readonly_roles))
                   | (set(draft_roles) & set(readonly_roles))
                   | (set(gate_roles) & set(draft_roles)))
        if overlap:
            problems.append(
                f"[角色重叠] {name} 的岗位被同时声明成两种角色：{sorted(overlap)}")

        for rid in gate_roles:
            _, got = _kernel_tools(agents.get(rid) or {})
            if not (GATE_TOOLS & got):
                problems.append(
                    f"[门岗无门] {name}/{rid} 被声明为持门岗，但工具面里没有确认门工具")
            stray = got - GATE_TOOLS - READONLY_TOOLS
            if stray:
                problems.append(
                    f"[门岗越界] {name}/{rid} 持门岗带了既非门也非只读的工具 {sorted(stray)}")
        for rid in readonly_roles:
            _, got = _kernel_tools(agents.get(rid) or {})
            bad = sorted(WRITE_TOOLS & got)
            if bad:
                problems.append(
                    f"[只读岗越权] {name}/{rid} 声明为只读却持有写工具 {bad}")
            gates = sorted(GATE_TOOLS & got)
            if gates:
                # 只读岗持门比持写更严重：写至少要过门，门在只读岗等于把门给了
                # 不参与审读的席位。这两条分开报，因为修法不同。
                problems.append(
                    f"[只读岗持门] {name}/{rid} 声明为只读却持有确认门 {gates}")
        for rid in draft_roles:
            _, got = _kernel_tools(agents.get(rid) or {})
            if GATE_TOOLS & got:
                problems.append(
                    f"[拟稿岗持门] {name}/{rid} 是拟稿岗却持有确认门 {sorted(GATE_TOOLS & got)}")
            if not (WRITE_TOOLS & got):
                problems.append(
                    f"[拟稿岗无写面] {name}/{rid} 声明为拟稿岗，却没有任何写工具")
        for aid in agents:
            narrowed, got = _kernel_tools(agents[aid])
            if not narrowed:
                problems.append(
                    f"[未逐岗收窄] {name}/{aid} 没有 resources.mcpTools")
                continue
            for t in sorted(got - tools):
                problems.append(f"[工具悬空] {name}/{aid} 授权了工具面里没有的 {t!r}")

    return {"ok": not problems, "problems": problems, "teams": teams,
            "declared": declared}


#: 不参与团队扫描的顶层目录（不是板块）
_NON_BOARD = frozenset({"scripts", "tooling", "docs", "node_modules", ".git",
                       ".github", "dist", "build"})


def _erp_board_teams(erp_dir: Path = ERP) -> set[str]:
    """ERP 棋盘团队（`erp/` 下带 fleet 段的模板）——已由 check() 的岗位后缀判据管住。"""
    out: set[str] = set()
    if not erp_dir.is_dir():
        return out
    for tpl in sorted(erp_dir.glob("*/template.json")):
        try:
            body = json.loads(tpl.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("fleet"):
            out.add(tpl.parent.name)
    return out


def _default_boards() -> list[Path]:
    """默认扫全部板块：顶层目录里凡有 `*/template.json` 的都算。"""
    out: list[Path] = []
    for d in sorted(REPO.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in _NON_BOARD:
            continue
        if any(d.glob("*/template.json")):
            out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 与 check_all.py 的外仓闸调用约定同形：`<script> check`。多余的子命令词忽略，
    # 免得为了一个词在 fd-erp 侧写特例分支。
    if argv and argv[0] == "check":
        argv = argv[1:]
    ap = argparse.ArgumentParser(description="ERP 团队覆盖闸（跨仓只读）")
    ap.add_argument("--matrix", default=str(MATRIX))
    ap.add_argument("--tool-surface", default=str(TOOL_SURFACE))
    ap.add_argument("--erp-dir", default=str(ERP))
    ap.add_argument("--board", action="append", default=None,
                    help="跨板块治理要扫的板块目录（可重复）；不给则扫全部板块")
    ap.add_argument("--skip-governance", action="store_true",
                    help="只跑 ERP 棋盘覆盖判据（调试用）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = check(Path(args.matrix), Path(args.tool_surface), Path(args.erp_dir))

    gov = {"ok": True, "problems": [], "teams": 0, "declared": {}}
    boards = [] if args.skip_governance else (
        [Path(b) for b in args.board] if args.board else _default_boards())
    if boards:
        exempt = _erp_board_teams(Path(args.erp_dir))
        gov_teams = 0
        for board in boards:
            if not board.is_dir():
                result["problems"].append(f"[出处缺失] 板块目录不在位：{board}")
                continue
            part = check_governance(board, Path(args.tool_surface), exempt=exempt)
            gov_teams += part["teams"]
            gov["problems"].extend(part["problems"])
            gov["declared"].update(part["declared"])
        gov["teams"] = gov_teams
        gov["ok"] = not gov["problems"]
        result["problems"].extend(gov["problems"])

    payload = dict(result)
    payload["governance"] = gov
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("ERP 团队覆盖闸 · 21 格矩阵 ↔ 团队编制 ↔ 工具面")
        print(f"  矩阵格 {result['cells']} / 团队 {result['teams']} / "
              f"团队声明覆盖 {len(result['covered'])} 格")
        if boards:
            scope = "全部板块" if not args.board else ", ".join(b.name for b in boards)
            print(f"  跨板块治理：{scope}下 {gov['teams']} 个绑内核团队"
                  f"（{len(gov['declared'])} 份 _governance 声明）")
        if result["ok"] and gov["ok"]:
            print("-" * 62)
            print("✓ 通过 — 可用格各有承担者、三岗齐、逐岗收窄、确认门只在拍板岗；"
                  "绑内核团队治理声明齐全")
        else:
            print("-" * 62)
            for p in result["problems"]:
                print(f"  x {p}")
            print(f"阻断：{len(result['problems'])} 处")
    if result["problems"] and result["cells"] == 0:
        return 2
    return 0 if (result["ok"] and gov["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
