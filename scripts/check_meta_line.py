#!/usr/bin/env python3
"""元线三包的结构门（openspec: add-meta-fleet-line / §6 E1+E2）。

只查**结构**，不查内容好坏。五条断言，每条都必须能红：

  M1 三支柱：包级 `memory` 四轴齐（store/read/write/process）+ 每岗 `memory.guidance`（≤600 字）
  M2 工具面：每个 agent 都有 `resources.mcpTools`，且非空
  M3 看板：有 `board` 段且非空
  M4 写面禁令：任何 allowTools 里不得出现发布 / 实例化 / 启停类工具
  M5 节奏错峰：同一包内相邻岗位的 cron 间隔 ≥30 分钟（同 cell 串行队列 + 单回合时限）
  M6 模板变量：persona 正文里不得出现 `{{...}}`（persona 会被当模板渲染 ⇒ 部署期就炸）
  M7 报数配额：①同一岗一轮内不得要求报两次；②同一岗 cron 在任一 12h 窗口内点火 ≤3 次
     ——②**是本线的自律上限（成本与可读性），不是平台配额**。2026-11-13 平台修好了「每轮首条豁免」
     （判据从会话改成回合起点，镜像 sha-abb7e39 起上产），此后每轮的第一条报数**不再**受
     FLOOD_MAX_PER_WINDOW 约束，点火次数本身不触发限流。保留②只为防「一个岗把一天的 token
     堆进同一个上午窗口」，不是因为它会被平台拒。

为什么这五条：前三条是「三支柱不自证不发布」的可机检形态（实测 47 个包里 0 个声明 memory、
3 个用过逐岗工具面、0 个模板声明 board——不在纸上的东西永远是"有空再补"）；
M4 是元线唯一的权限边界（元线是唯一有能力绕开自己门的角色）；
M5 来自 2026-11-08 的真事故（全量取数 → compaction 75s → 模型流挂死 → 回合超时）。

用法：
    python3 fleet-packs/scripts/check_meta_line.py            # 跑三包
    python3 fleet-packs/scripts/check_meta_line.py --self-test # 五臂负向对照
退出码：0=全绿；1=有红；2=制品读不到。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

META_PACKS = {
    "构造": "shared/harness-constructor/manifest.json",
    "运维": "blueprints/meta-optimizer/manifest.json",
    "评测": "shared/meta-grader/manifest.json",
}

MEMORY_AXES = {"store", "read", "write", "process"}
GUIDANCE_MAX = 600
MIN_GAP_MINUTES = 30
# 平台参数：paas/server/fleet-reports.js `FLOOD_MAX_PER_WINDOW=3`，**窗口不是常数**——
# `floodWindowMs = max(cron 周期 ÷ 2, 10 分钟)`：日频 720 分钟、周频 5040、月频≈21600。
# （2026-11-14 实测判词就是 `report limit reached (3 per 5040 minutes)`，而三包正文当时
#  一律写「3 条 / 720 分钟」——周频岗被低估 7 倍，会让人以为配额 12 小时就重置。）
# 注意它**只约束同一轮的第二次及以后**——每轮首条自 2026-11-13 起是豁免的（按回合起点判）。
# 所以这里仍把「3 条」当本线自律上限、把 720 分钟当**日频**的参照值，而不是当平台边界。
REPORT_QUOTA = 3
REPORT_WINDOW_MIN = 720
# 「首报」与「末报」两类措辞同时出现 ⇒ 该岗被要求一轮报两次
FIRST_REPORT_HINTS = ("先报数", "立刻报数", "开工先 report_status", "开工即 `report_status`",
                      "拿到第一条证据就")
LAST_REPORT_HINTS = ("收尾必 report_status", "收尾再报", "收尾再报一次")
# 明确的禁令/一次性说明：出现这些就说明作者是在**禁止**双报，不是要求
REPORT_BAN_HINTS = ("不要收尾再补", "绝不在收尾再报", "不另报", "一轮只报一次", "每轮一次",
                    "报数**每轮一次**", "不是每回合")

# ── M10 能力承诺可执行 ───────────────────────────────────────────────────────
SQL_TOOL_RE = re.compile(r"sql|query|db_|_db|duckdb|read_query", re.I)
SQL_CLAIM_RE = re.compile(r"SQL\s*真值|查询算|直接查|查库|跑\s*SQL|用\s*SQL")
SQL_DISCLAIM_RE = re.compile(r"没有\s*SQL|无\s*SQL|没有查询工具|无查询工具|别去找")
# 每岗回合预算：平台 `cron-runner.js` 把它夹在 [2min, 30min]（floor 防手误 0、ceiling 限
# 一个岗占住共享 child 的时长）。**夹紧是静默的**——写 45min 会变成 30min、写 60s 会变成
# 2min，而包里那行字看着没变，正是「改了不生效」那一类。
TURN_BUDGET_MIN_MS = 120_000
TURN_BUDGET_MAX_MS = 1_800_000
# M18：逐队拉回合史那一族的预算下限。取 900000（15 分钟 ⇒ 软停 720s）不是拍的：
# 同线已声明此值的三岗实测 438s/524s/531s 收口，而缺省 600s 的软停是 480s。
HEAVY_READ_TOOL = "fleet_turns_read"
HEAVY_READ_MIN_MS = 900_000
# 「至多 N 支/条/个」—— 同一岗两份正文的分批上限必须一致（M12）
# 两种措辞都算定量上限：『至多 N 支』与『只对拍 N 支』。第一版只认「至多」，于是
# 根本没看上 persona 那句「单回合只对拍 **1 支**团队」——判据绿着，缺陷就在眼前（假绿）。
BATCH_CAP_RES = (
    re.compile(r"至多\s*\*{0,2}(\d+)\s*\*{0,2}\s*[支条个]"),
    re.compile(r"只(?:对拍|评测|评|读|做|取|覆盖|处理)\s*\*{0,2}(\d+)\s*\*{0,2}\s*[支条个]"),
)


def _batch_cap(text: str) -> "re.Match[str] | None":
    for rx in BATCH_CAP_RES:
        m = rx.search(text)
        if m:
            return m
    return None

# 已被证伪的旧口径：「一回合/每回合最多 N 次」。真机实测（turn 1631）是 3 条 / 720 分钟、
# 按岗跨轮共享——一轮里报第二条就会被拒，所以写成「每回合 3 次」等于教一个岗在 1.5 轮内
# 把 12 小时额度烧光。manifest 已于 2026-11-13 改掉，但 blueprint.json 那份漏改，而且
# **没有任何闸扫 blueprint**（E1' 查的是另一种八段发布对象，M7 只扫三份 manifest）。
# 这条判据补的就是那个洞。命中后允许「引用旧口径来反驳」——见 CALIBER_REFUTED。
PACK_ID_RE = re.compile(r"\{\{PACK_ID:[^}]*\}\}")
PER_TURN_REPORT = re.compile(r"(?:一回合|每回合|单回合)[^。；\n]{0,16}?最多[^。；\n]{0,10}?(\d+)\s*次")
CALIBER_REFUTED = ("旧口径", "不是每回合", "已被证伪", "证伪", "720", "12 小时")
# 蓝图（构造师的产物格式）：E1' 的八段发布对象不是它，M7 的 manifest 也不是它。
# 存在才查——不是每支舰队都有蓝图。
BLUEPRINTS = {
    "运维蓝图": "blueprints/meta-optimizer/blueprint.json",
    "评测蓝图": "shared/meta-grader/blueprint.json",
}

# 发布 / 实例化 / 启停：元线一律不得持有
FORBIDDEN_TOOLS = {
    "facet_market_pack_publish", "facet_market_pack_visibility", "facet_market_pack_curation",
    "fleet_apply",
    "wanxing.agent.pause", "wanxing.agent.resume", "wanxing.agent.deploy",
}
FORBIDDEN_SUBSTRINGS = ("_publish", "fleet_apply", ".pause", ".resume", ".deploy")


def _cron_minutes(expr: str) -> int | None:
    """只支持 `M H * * *` / `M H * * D`（本线全部是这种）。返回当日分钟数。"""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    try:
        minute, hour = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return hour * 60 + minute


def _runs_per_window(expr: str, window_min: int) -> int | None:
    """该 cron 在任意 window_min 分钟内最多点火几次。判不了返回 None（判不了 ≠ 通过）。

    只做**保守下界不可算就报错**的换算：支持 `M H * * *`（每日）、`M H * * D`（每周）、
    以及形如 `*/N` 的高频分钟/小时表达式（用最小间隔算窗口内次数）。
    """
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    minute, hour = parts[0], parts[1]

    def _step(field: str, span: int) -> int | None:
        """返回该字段的最小推进步长（分钟或小时）。"""
        if field == "*":
            return 1
        if field.startswith("*/"):
            try:
                return max(1, int(field[2:]))
            except ValueError:
                return None
        return None

    m_step, h_step = _step(minute, 60), _step(hour, 24)
    if m_step is not None and h_step is not None:
        # 高频：最小间隔 = h_step 小时 或 m_step 分钟（取小者）
        gap = min(h_step * 60, m_step)
        return max(1, window_min // gap) if gap > 0 else None
    # 定点表达式：每 (日|周) 一次
    try:
        int(minute); int(hour)
    except (TypeError, ValueError):
        return None
    return 1



def check_placeholders(name: str, manifest: dict) -> list[dict]:
    """M8 未解析的 pack-id 占位符：`{{PACK_ID:<名>}}` 不许活到发布产物里。

    2026-11-13 实测：`shared/meta-grader/manifest.json` 的
    `fleet.members[0].target.packId` 是字面量 `{{PACK_ID:meta-grader}}`，而市场里安装的
    v3 也带着同一个字面量——于是「评测舰队」**永远建不起来**（provisioning 解析不出
    preset 目标），而三处判据都看不见它：漂移哨按台账行遍历、这台账里根本没有
    meta-grader 行；蓝图门只查 dataSources/mcpServers 的引用；`cast_blueprint.py`
    没有占位符解析（`cast_fleet_template.py` 有，line 157 会直接抛）。

    判据：扫描整份 manifest（`_casting.note` 除外——那里 `{{PACK_ID:*}}` 是约定写法的
    示例文本），命中即红并指名 json 路径。
    """
    errs: list[dict] = []

    def walk(node, path: str = ""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for m in PACK_ID_RE.finditer(node):
                yield path, m.group(0)

    for path, tok in walk({k: v for k, v in manifest.items() if k != "_casting"}):
        errs.append({"pack": name, "code": "pack_id_placeholder",
                     "error": (f"{path} 里留着未解析的 {tok}——发布产物必须是字面 packId。"
                               f"这个包若已上架，说明上架的就是这份坏产物："
                               f"fleet 里指向它的 preset 目标解析不出来（评测舰队就是这么建不起来的）"),
                     "entry": path})
    return errs


def check_member_triggers(name: str, manifest: dict) -> list[dict]:
    """M9 编排边自洽：端点必须存在、trigger 的时刻必须等于岗位节律。

    ⚠️ 这里**故意不查**「有 cron 就必须有入边」。第一版这么写了，并且在 meta-grader 的
    审计岗上判红——去修的时候被市场当面拒掉：
    `auditor_not_orchestratable: 审计官不进工作图：它复核判断，不接活也不派活`。
    再回源码核对，真正的派发是按成员建任务的（`fleet-reconcile.js` 的 `reconcileFleet`
    对**每个有 cadence 的成员**建定时任务，与编导边无关），审计官更是被编排校验显式拒绝
    当边端点（`fleet-orchestration.js` 174-190）。所以「没有入边＝永不点火」是错的假设，
    拿它当判据会把一切合规的审计官编制判红。

    剩下两条是真判据（都会让编排图静默失效或按错节律跑）：
      M9a 边指向/来自的 ref 必须是舰队里存在的成员（拼错一个字，边静默失效）；
      M9b `trigger cron(X)` 的 X 必须等于目标成员自己的 cron（两处节律不一致时按哪个跑？）。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    fl = manifest.get("fleet")
    if not isinstance(fl, dict):
        return errs
    members = [m for m in (fl.get("members") or []) if isinstance(m, dict)]
    if not members:
        return errs
    refs = {m.get("ref") for m in members if m.get("ref")}
    cron_of = {m.get("ref"): (m.get("cadence") or {}).get("cron") for m in members}
    edges = [e for e in ((fl.get("orchestration") or {}).get("edges") or []) if isinstance(e, dict)]

    for i, e in enumerate(edges):
        kind, frm, to = e.get("kind"), e.get("from"), e.get("to")
        for side, val in (("to", to), ("from", frm)):
            if not isinstance(val, str):
                err("edge_endpoint_invalid", f"第 {i} 条边（{kind}）的 {side} 不是字符串：{val!r}",
                    f"fleet.orchestration.edges[{i}].{side}")
                continue
            if val.startswith(("cron(", "event(", "webhook(")):
                continue
            if val not in refs:
                err("edge_ref_unknown",
                    f"第 {i} 条边（{kind}）的 {side}={val!r} 不是本舰队成员——"
                    f"拼错一个字这条边就静默失效（现有成员 {sorted(r for r in refs if r)}）",
                    f"fleet.orchestration.edges[{i}].{side}")
        # M9b：cron 触发器的时刻必须就是目标岗自己的节律
        if kind == "trigger" and isinstance(frm, str) and frm.startswith("cron(") \
                and isinstance(to, str) and to in cron_of:
            inside = frm[5:-1].strip() if frm.endswith(")") else frm[5:]
            if cron_of.get(to) and inside != cron_of.get(to):
                err("edge_cron_mismatch",
                    f"第 {i} 条 trigger 写的是 cron({inside})，而岗位 '{to}' 自己的 cron 是 "
                    f"{cron_of.get(to)!r}——两处节律不一致时没人知道按哪个跑",
                    f"fleet.orchestration.edges[{i}].from")
    return errs


def check_capability_executable(name: str, manifest: dict) -> list[dict]:
    """M10 persona 承诺的能力必须真的在工具面里。

    2026-11-14 首轮实测：评测岗 persona 第一条写「SQL 真值：…直接用查询算」，而它的工具面
    只有 `fleet_list` / `fleet_spec_read` / `fleet_turns_read` ＋市场两读——**没有任何 SQL
    或查询工具**。结果模型在工具面里找通道（`lastTool=list_mcp_resource_templates`），
    19 步把 480s 的回合预算烧光，`hasOutput=false`、`reported=false`、一条报数都没有。
    同一类坑此前也出现过：盘账岗要求「落 board 草稿」而它没有 board 工具（v5 才改成
    「本岗无 board 工具」）。两次都不是结构错，**是承诺了一个不存在的能力**，所以任何结构
    闸都看不见——直到它真发车。

    判据只有一条：声称「SQL 真值 / 查询算 / 直接查库」且同句里没有「没有 SQL 通道 /
    无 SQL 工具 / 别去找」这类否定 ⇒ 工具面里必须有一个 SQL 类工具。**否定只在同一句内
    生效**——跨句放宽会把「本岗无 board 工具」这类声明的作用域放大到整段，替真违规洗白。

    为什么不查「反引号点名的工具必须在工具面里」：试过，它把 `teams_graded` / `items_cited`
    / `decision_queue` / `dissent_reasons` 全判红了——本仓的反引号 snake_case 绝大多数是
    **报数键与台账名**，不是工具名。离线分不出这两者（要分就得先有一份工具名词典），
    判据写不准就会常年红、然后被无视。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        tools = set()
        for ref in (a.get("resources") or {}).get("mcpTools") or {}:
            tools |= set((a.get("resources") or {})["mcpTools"][ref] or [])
        text = "\n".join([a.get("persona") or "",
                          (a.get("cadence") or {}).get("promptTemplate") or "",
                          (a.get("memory") or {}).get("guidance") or ""])
        # 声称有 SQL/查库通道
        has_sql_tool = any(SQL_TOOL_RE.search(t) for t in tools)
        for sent in re.split(r"[。；\n]", text):
            if not SQL_CLAIM_RE.search(sent):
                continue
            if SQL_DISCLAIM_RE.search(sent):
                continue
            if has_sql_tool:
                continue
            err("capability_without_tool",
                f"岗位 '{aid}' 声称有 SQL/查库通道（原句「{sent.strip()[:60]}」），"
                f"但工具面里没有任何 SQL 类工具（现有 {sorted(tools)}）"
                f"——真机会表现为在工具面里找通道、把整轮预算烧光且零报数",
                f"agents[{aid}].persona")
    return errs


def check_verdict_channel(name: str, manifest: dict) -> list[dict]:
    """M15 有 cron 的岗必须有一个**收口得了**的结论通道。

    `auditor_submit_verdict` 复核的是**待决事项**（decision item，`itemId` 必填，服务端按
    `getDecision` 查）。评测结论不产生待决事项：2026-11-14 实测审计岗两次提交都返回
    「Decision not found」（本舰队 `openDecisions: 0`），随后它判定「没有可提交的条目」空手收尾。
    也就是说——把复核结论挂在这个工具上是**结构上走不通**的主路径。

    判据：有 cron 的岗，`mcpTools` 里若声明了 `auditor_submit_verdict`，必须同时声明
    `report_status`（否则它唯一的结论出口是那个需要 itemId 的工具，必然空手）。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        if not ((a.get("cadence") or {}).get("cron")):
            continue
        tools = set()
        for _ref, lst in ((a.get("resources") or {}).get("mcpTools") or {}).items():
            if isinstance(lst, list):
                tools |= set(lst)
        if "auditor_submit_verdict" in tools and "report_status" not in tools:
            err("verdict_channel_unreachable",
                f"岗位 '{aid}' 有 cron，结论只挂在 `auditor_submit_verdict` 上（它要 `itemId`，"
                "而本条线的结论不产生待决事项 ⇒ 实测两次「Decision not found」后空手收尾）。"
                "把 `report_status` 一并给它，并把裁决写进同一条报数的三槽",
                f"agents[{aid}].resources.mcpTools")
    return errs


# 报数信封字段：这些词会出现在报数句里，但它们不是槽位键（报数工具自己收的）。
ENVELOPE_NON_SLOT = frozenset({"summary", "resources", "blockers", "details", "metrics"})
# 平台工具名：它们也常紧挨着「槽」字出现（"用 report_status 报 X 槽"），但工具名永远不是
# 报数槽。只登记平台自带的这几个，不做「名字像工具就当工具」的推断。
PLATFORM_TOOLS = frozenset({
    "report_status", "auditor_submit_verdict", "tool_search",
    "fleet_list", "fleet_spec_read", "fleet_turns_read",
})
# 「N 槽」前面那一串 id 才是槽位声明。分隔符切段是为了不吃到同句里前一段的
# 裁决枚举——`agree/dissent/block` 是三态取值，不是槽位名，不去段就会把它们判红。
_SLOT_CHUNK_CUT = re.compile(r"[；;。\n]")
# 词界两侧都要卡：否则 `kpiSlots` 会被切成 `kpi` + `lots` 两个假槽位（2026-11-14 实测，
# 我把「`gold_source` 不是槽位…槽位只认 kpiSlots 声明过的键」写进正文后，闸立刻报
# 三个不存在的槽：gold_source / kpi / lots）。
_SLOT_ID_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9_]{2,}(?![A-Za-z0-9_])")
# 三种「正文里点名槽位」的写法，各一条判据：
#   ① `verdicts` / `blocks` / `dissent` 三槽      —— 紧邻「N 槽」，取「槽」前面那一串
#   ② 报 `segments_done` 与 `segments_left`       —— 「报 X 与 Y」，不带「槽」字
#   ③ 报数含 proposals_raised / items_cited / …   —— 「报数含」后的一串（提案岗就是这种）
# 第一版只看「槽」字前方 120 字窗口，结果把九段名里的 `fleet`（boundary→dataSources→fleet→…）
# 当成槽位判红了——窗口太宽会把同句的普通标识也吸进来。改成只认紧邻的 / 分隔串。
_SLOT_DECL_RE = re.compile(
    r"((?:[a-z][a-z0-9_]{2,}[`\s]*[/、][`\s]*)+[a-z][a-z0-9_]{2,})[`\s]*"
    r"(?:[一二三四五六七八九十\d]+\s*)?(?:个)?槽")
_SLOT_AND_RE = re.compile(r"报\s*`([a-z][a-z0-9_]{2,})`\s*与\s*`([a-z][a-z0-9_]{2,})`")
# 「报数含 A / B / C」：只认这个字面前缀**紧跟的那一串 / 分隔 id**，不取整句。第三种写法
# 是 2026-11-14 由线上产物侧对账（check_live_fleets.py --slots）先发现的：提案岗正文写了
# 四个槽、`kpiSlots` 只声明三个，`pending_next_round`（排队提案数）从来没上过卡片。
# 取整句（到句读为止）会把后面的解释性括注一起吸进来——那里的 `gold_source` 是**在说它
# 不是槽位**，按槽位判红就是把否定读成了承诺。
# 「报数含 A / B」与「报数（A / B」两种都有（提案岗两处正文各用一种）。前缀后只许 6 个
# 非标识字符（空格、`（`、`**`），够吃掉这层包装，又不够吸到下一句的解释性括注。
# 「含」是 CJK 字、被 \w 收进来，所以要显式允许；其余前缀字符只许 6 个非标识字符
# （空格、`（`、`**`）——够吃掉这层包装，又不够吸到下一句的解释性括注。
_SLOT_LIST_RE = re.compile(
    r"报数(?:含)?[^\w\n]{0,6}(`?[a-z][a-z0-9_]{2,}`?(?:\s*[/、]\s*`?[a-z][a-z0-9_]{2,}`?)+)")


def check_declared_slots(name: str, manifest: dict) -> list[dict]:
    """M16 正文里承诺的报数槽，必须在 `kpiSlots` 里声明过。

    服务端（`server/fleet-mcp.js` 的 toolReport）只把 `kpiSlots` 声明过的键算作
    `slotsAccepted`，其余键回一句「UNRECOGNIZED slot keys (kept in details, not shown on
    the card)」就收进 details——**不报错、不失败**。于是「正文说报三槽、编制只声明两槽」
    这种错在真跑里完全静默：2026-11-14 实测审计岗正是这一形态（persona/promptTemplate 都写
    `dissent_reasons`，而 kpiSlots 只有 `verdicts`/`blocks`），report 的 metrics 里三槽齐全，
    卡片上永远只有两槽。

    判据只认「槽」这个字前面的那段 id 串（按 `；;。\\n` 切段），并且把工具名与信封字段
    排除在外——不做「看着像槽位」的推断，因为推断出来的槽位会替错声明背书。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    any_declared: set[str] = set()
    for m in (manifest.get("fleet") or {}).get("members") or []:
        for s in m.get("kpiSlots") or []:
            k = s.get("key") if isinstance(s, dict) else str(s)
            if k:
                any_declared.add(k)
    if not any_declared:
        return errs

    not_slots = set(ENVELOPE_NON_SLOT) | set(PLATFORM_TOOLS)
    for s in manifest.get("mcpServers") or []:
        not_slots.add(s.get("registryName") or "")
        not_slots |= {t for t in (s.get("allowTools") or []) if isinstance(t, str)}
    for a in manifest.get("agents") or []:
        for ref, lst in ((a.get("resources") or {}).get("mcpTools") or {}).items():
            not_slots.add(ref)
            if isinstance(lst, list):
                not_slots |= {t for t in lst if isinstance(t, str)}

    agent_of = {a.get("id"): a for a in manifest.get("agents") or [] if isinstance(a, dict)}

    # 逐岗比，不比全局并集：槽位是**这张卡**的字段，别的岗声明过不等于本岗的卡会显示它。
    # 用并集会漏报——「A 岗声明了 X，B 岗也报 X」会被当成没问题。
    for m in (manifest.get("fleet") or {}).get("members") or []:
        ref = m.get("ref")
        cad = m.get("cadence") or {}
        if not cad.get("cron"):
            continue
        declared = {s.get("key") if isinstance(s, dict) else str(s)
                    for s in (m.get("kpiSlots") or []) if s}
        a = agent_of.get(ref) or {}
        texts = [cad.get("promptTemplate") or "", a.get("persona") or "",
                 a.get("promptTemplate") or "", (a.get("cadence") or {}).get("promptTemplate") or ""]
        named: list[tuple[str, str]] = []
        for text in texts:
            for hit in _SLOT_DECL_RE.finditer(text):
                for sid in _SLOT_ID_RE.findall(hit.group(1)):
                    named.append((sid, hit.group(0)))
            for hit in _SLOT_AND_RE.finditer(text):
                named.append((hit.group(1), hit.group(0)))
                named.append((hit.group(2), hit.group(0)))
            for hit in _SLOT_LIST_RE.finditer(text):
                for sid in _SLOT_ID_RE.findall(hit.group(0)):
                    named.append((sid, hit.group(0)[:80]))
        for sid, where in named:
            if sid in declared or sid in not_slots:
                continue
            err("slot_undeclared",
                f"岗位 '{ref}' 的正文承诺了报数槽 `{sid}`（原文「{where}」），但它的 `kpiSlots` "
                f"没声明这个键（本岗已声明 {sorted(declared)}）——服务端会把它放进 details 而不上卡片，"
                "且**不报错**：真跑里看到的是一份「槽位齐全」的 metrics 和一张少槽的卡",
                f"fleet.members[{ref}].kpiSlots")
    return errs


# 写面承诺的词表：正文说这些事，就必须有一个真能落盘的通道。
# 2026-11-14 实测（技能作者 4 连败、context pressure 压缩 4 次、从未报数）：
# 它的正文写「为蓝图补 skills[] … 后收口 diff 停等批准」，而工具面只有
# `fleet_spec_read` —— `fleet_draft` 的 schema 是**舰队编制**
# （mode/fleetId/members[{op,ref,title,targetKind,cadence}]），根本不写 skills[] 正文；
# 真能写蓝图的是 blueprint-author 服务（blueprint_validate / blueprint_save），它一个没有。
# 于是模型只能拿 fleet_spec_read 把整份蓝图反复拉进上下文「读着改」，4 次压缩后超时。
# 只认**写动词短语**，不认裸名词：第一版把「草稿」也算承诺，于是「拿到草稿」「读草稿」的
# 盘账岗/装配岗全被判红——那是在读，不是在写（同族于 M16 第一版把九段名当槽位）。
# 提审职责的**正面**说法（本岗自己提）。只认这几种字面，不认「提案」这类名词：
# 名词在「铸造提案由装配岗提交」里会出现，按名词判会把不提案的岗也算成提案岗。
_DUTY_LITERAL = (
    "由本岗提审", "本岗提审", "由本岗提审：",
    "由本岗用 `propose_action`", "由本岗用 propose_action",
    "本岗用 `propose_action`", "本岗用 propose_action",
    "提交铸造提案",
    # 提案岗是以「出提案」为职责的岗（`meta-optimizer/mo-proposal` 就是这么写的：
    # 标题即「提案岗」、正文「只对已确认缺陷出提案」）。按名词「提案」判会把它算成无职责，
    # 因为它同时会说「被拒提案」「重复提同一条」——所以只认动词短语与岗位名。
    "提案岗", "出提案", "提提案", "提交提案", "发提案",
)


_NEG_CHARS = "不勿禁毋无免别"


def _claims_propose_duty(text: str) -> bool:
    """正文是否**正面**声称「由本岗提审 / 本岗出提案」。

    否定前缀必须挡掉：`mg-eval` 的职责边界写「不修被评对象、不出提案」——按子串判会把
    「评测岗不准提提案」读成「评测岗负责提提案」，正好判反（同族于 M16 的括注否定坑）。
    """
    for lit in _DUTY_LITERAL:
        start = 0
        while True:
            i = text.find(lit, start)
            if i < 0:
                break
            if i == 0 or text[i - 1] not in _NEG_CHARS:
                return True
            start = i + 1
    return False


def check_write_channel(name: str, manifest: dict) -> list[dict]:
    """M17（重写）**提审权的归属**：只在真提审的岗上，且一队至多一个岗持有。

    这一格原来判的是「正文承诺落盘 ⇒ mcpTools 里要有能落盘的那一件」，候选三件分别是
    `fleet_draft` / `blueprint_save` / `propose_action`——**三件都不是落盘**，读源码即知：

    · `fleet_draft` 是**干跑**（`server/fleet-architect-mcp.js` 的工具描述原文：
      "Dry-run a spec change … Nothing is persisted"）；
    · `blueprint_save` 属另一台 MCP（`blueprint-author-mcp`，管的是**模板**），它的纪律
      明写「approval, minting and trials happen in the Blueprint Studio by the owner,
      never through you」；
    · `propose_action` 是**提审**（进拍板队列），不是落盘。

    岗往工作区写文件走的是运行时**内建文件工具**（`str_replace_editor`、bash 重定向/heredoc，
    见 paas `server/file-tool-paths-server.js` 的 `MODE_COMMAND_TOOLS` 与 `REDIRECT_RE`），
    它们不在 `mcpTools` 里。所以旧判据**结构上不可能为绿**，唯一能让它变绿的动作就是
    「发一个提审权」——2026-11-14 实况正是这么长出来的：技能作者缺写面 ⇒ 给它
    `propose_action` ⇒ 闸绿；而被设计指定去提审的装配岗反倒没有这件工具，另外两个岗却都有。
    后果不是「少一件工具」，而是同一份发布对象会被多个岗各提一次、各进一次审批队列。

    改写后判三件真事（同族于 M2/M20 的「面上写了、手里没有」）：
      R1 `propose_tool_without_duty`：工具面有提审权，正文没有任何「由本岗提审」⇒ 谁都能提；
      R2 `propose_duty_without_tool`：正文说「由本岗提审」而工具面没有 ⇒ 说了也做不到；
      R3 `multiple_proposers`：一队里多于一个岗持提审权 ⇒ 同一对象重复进队列。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    holders: list[tuple[str, bool]] = []
    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        if not ((a.get("cadence") or {}).get("cron")):
            continue
        tools: set[str] = set()
        for _ref, lst in ((a.get("resources") or {}).get("mcpTools") or {}).items():
            if isinstance(lst, list):
                tools |= set(lst)
        text = " ".join([a.get("persona") or "", a.get("promptTemplate") or "",
                         (a.get("cadence") or {}).get("promptTemplate") or ""])
        has_tool = "propose_action" in tools
        has_duty = _claims_propose_duty(text)
        holders.append((aid, has_tool))
        if has_tool and not has_duty:
            err("propose_tool_without_duty",
                f"岗位 '{aid}' 的工具面里有 `propose_action`，但正文没有任何「由本岗提审」的职责——"
                "提审权挂在没这项职责的岗上，等于「谁都能提」，同一份发布对象会被各岗各提一次"
                "（2026-11-14 实况：架构/技能两岗持提审权，而被指定提审的装配岗没有）",
                f"agents[{aid}].resources.mcpTools")
        if has_duty and not has_tool:
            err("propose_duty_without_tool",
                f"岗位 '{aid}' 的正文说「由本岗提审」，而工具面（{sorted(tools)}）里没有 "
                "`propose_action`——说了也做不到，轮到它提审时只能自己发明通道或空转",
                f"agents[{aid}].resources.mcpTools")

    if len([h for h in holders if h[1]]) > 1:
        err("multiple_proposers",
            f"本队有多个岗持 `propose_action`：{[h[0] for h in holders if h[1]]}——"
            "同一份产物会被各岗各提一次、各进一次审批队列（重复铸造）。提审权收敛到一个岗"
            "（构造线是装配岗，它交的才是完整八段发布对象）；确需第二处提审面时，先改这条判据并记账",
            "agents[].resources.mcpTools")
    return errs


def check_heavy_read_budget(name: str, manifest: dict) -> list[dict]:
    """M18 正文指挥「逐队拉回合史」的岗，必须显式声明回合预算。

    缺省值不是中性的：平台缺省 10 分钟、软停落在 **480s**（`cron-runner.js`），而
    2026-11-14 实测同类回合的真实耗时是 **438s / 524s / 531s**——`fleet_turns_read`
    一次回包就带 20 条截断事件，逐队拉 3-5 队必然越过 480s。所以「没写预算」在本线
    不是「用默认值」，是「把一轮的成果押在 42 秒的边际上」。

    判据只看一个信号：正文（persona + 两处 promptTemplate）里出现 `fleet_turns_read`。
    它把两类岗分开得很干净——重读岗全都点了它的名，轻读岗（架构师/技能作者只读
    `fleet_spec_read` 一份编制）一个都没提，无需再判「读了几次」。

    线上实历（2026-11-14）：优化团队两岗连撞 `budget soft stop (480s of 600s)`，
    连跑两轮零报数；另一支同线舰队的同名岗因为声明了 900000 而以 524s/531s 收口。
    差别只在这一行，而离线闸当时没有任何一条会问它。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    fl = manifest.get("fleet")
    if not isinstance(fl, dict):
        return errs
    agent_cad = {a.get("id"): (a.get("cadence") or {}) for a in (manifest.get("agents") or [])
                 if isinstance(a, dict)}
    for m in fl.get("members") or []:
        if not isinstance(m, dict):
            continue
        ref = m.get("ref")
        cad = m.get("cadence") or {}
        if not cad.get("cron"):
            continue
        agent = next((a for a in (manifest.get("agents") or [])
                      if isinstance(a, dict) and a.get("id") == ref), None)
        if not isinstance(agent, dict):
            continue
        text = " ".join([agent.get("persona") or "", agent.get("promptTemplate") or "",
                         (agent.get("cadence") or {}).get("promptTemplate") or "",
                         cad.get("promptTemplate") or ""])
        if HEAVY_READ_TOOL not in text:
            continue
        val = cad.get("turnTimeoutMs")
        if val is None:
            err("heavy_read_no_budget",
                f"岗位 '{ref}' 的正文指挥逐队调 `{HEAVY_READ_TOOL}`，却没有声明 "
                f"`turnTimeoutMs`——缺省 10 分钟的软停是 480s，而实测同类回合 438-531s"
                f"（2026-11-14 优化团队两岗连撞 `budget soft stop (480s of 600s)`，两轮零报数；"
                f"同线声明了 {HEAVY_READ_MIN_MS} 的两岗以 524s/531s 收口）；建议 "
                f"`turnTimeoutMs: {HEAVY_READ_MIN_MS}`",
                f"fleet.members[{ref}].cadence.turnTimeoutMs")
            continue
        try:
            ms = int(val)
        except (TypeError, ValueError):
            continue  # 形状错误由 M11 报，不重复
        if ms < HEAVY_READ_MIN_MS:
            err("heavy_read_budget_low",
                f"岗位 '{ref}' 逐队调 `{HEAVY_READ_TOOL}` 而预算只有 {ms}ms（软停 {int(ms * 0.8)}s）"
                f"——低于实测下限（438-531s），建议 ≥ {HEAVY_READ_MIN_MS}",
                f"fleet.members[{ref}].cadence.turnTimeoutMs")
    return errs


def check_i18n_slots(name: str, manifest: dict) -> list[dict]:
    """M19 四语种槽位覆盖层必须「全译或整省」。

    真源是平台的包校验器（`paas/lib/pack-manifest.js`）：「translate every slot or omit
    the key」——覆盖层缺一个键就是 partial overlay，整份包拒收。而本仓的离线闸此前
    **一条都不查 i18n**，于是 2026-11-14 实测出现过这种形态：包里 architect 已声明
    四槽，四份语种覆盖层仍只写前两槽，`validate_manifest.py`（真校验器）报 4 条错，
    而 `check_meta_line.py` 全绿——闸绿着，包发不出去，或发出去了却没被人读过。

    只判两件事，都在 `fleet.i18n.<lang>.members[<ref>].kpiSlots` 上：
    ① 覆盖层出现就必须覆盖该岗**全部**已声明槽（缺键＝partial）；
    ② 不许出现该岗没声明的键（那是旧槽名残留，平台同样拒收）。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    fl = manifest.get("fleet")
    if not isinstance(fl, dict):
        return errs
    declared: dict[str, list[str]] = {}
    for m in fl.get("members") or []:
        if not isinstance(m, dict):
            continue
        declared[m.get("ref")] = [s.get("key") for s in (m.get("kpiSlots") or [])
                                  if isinstance(s, dict) and s.get("key")]
    i18n = fl.get("i18n")
    if not isinstance(i18n, dict):
        return errs
    for lang, block in i18n.items():
        members = (block or {}).get("members") if isinstance(block, dict) else None
        if not isinstance(members, dict):
            continue
        for ref, entry in members.items():
            if not isinstance(entry, dict) or "kpiSlots" not in entry:
                continue
            want = declared.get(ref)
            if want is None:
                err("i18n_slot_unknown_member",
                    f"`fleet.i18n.{lang}.members['{ref}']` 译了一个编制里没有的岗",
                    f"fleet.i18n.{lang}.members['{ref}']")
                continue
            got = [k for k in ((entry.get("kpiSlots") or {}) if isinstance(entry.get("kpiSlots"), dict)
                               else {})]
            missing = [k for k in want if k not in got]
            extra = [k for k in got if k not in want]
            if missing:
                err("i18n_slot_partial",
                    f"`fleet.i18n.{lang}.members['{ref}'].kpiSlots` 是 partial overlay——缺 "
                    f"{missing}（平台要求「每槽都译或整省该键」，partial 会被校验器拒收；"
                    f"实历 2026-11-14：architect 四槽而四语种只写两槽，真校验器 4 错、离线闸全绿）",
                    f"fleet.i18n.{lang}.members['{ref}'].kpiSlots")
            if extra:
                err("i18n_slot_stale",
                    f"`fleet.i18n.{lang}.members['{ref}'].kpiSlots` 含已不存在的槽 {extra}"
                    f"（该岗现声明 {want}）——旧槽名残留，平台拒收",
                    f"fleet.i18n.{lang}.members['{ref}'].kpiSlots")
    return errs


def check_toolface_shape(name: str, manifest: dict) -> list[dict]:
    """M20 逐岗工具面的两处形状错误：服务未在包级声明、工具名用点号写法。

    真源是包 schema（构造师自己的 `shared/harness-constructor/manifest.json` 就是这么写的）：
    逐岗工具面是**两层**结构 `resources.mcpTools = {registryName: [裸工具名, …]}`，
    服务由包级 `mcpServers[].registryName` 注册。

    ① 逐岗面里出现包级没声明的服务 ⇒ 该服务在部署期不会被注册，那几件工具会静默消失
       （与 M2/M17 同族：**面上写了、手里没有**）；
    ② 工具名写成 `server.tool` ⇒ 两种 schema 都不认（本仓 370 处 `allowTools` 全是裸名），
       铸造时要么被当成不存在的工具丢掉，要么整份包被判不合法。实历 2026-11-14：装配岗
       把草稿写成 `fd-memory.memory_write_page`（名字对、形状错），而当时的 M2 只判非空，
       **没有任何闸看值的形状**——这正是本谷要补的洞。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    top: list[str] = []
    for s in (manifest.get("mcpServers") or []):
        if isinstance(s, dict) and s.get("registryName"):
            top.append(str(s["registryName"]))

    for a in (manifest.get("agents") or []):
        if not isinstance(a, dict):
            continue
        mt = ((a.get("resources") or {}).get("mcpTools")
              if isinstance(a.get("resources"), dict) else None)
        if not isinstance(mt, dict):
            continue
        for srv, tools in mt.items():
            if top and srv not in top:
                err("toolface_server_undeclared",
                    f"岗位 '{a.get('id')}' 的工具面引用服务 `{srv}`，但包级 `mcpServers` 没声明它"
                    f"（现声明 {top}）——部署期该服务不注册，这几件工具会静默消失",
                    f"agents[{a.get('id')}].resources.mcpTools['{srv}']")
            for t in (tools or []):
                if isinstance(t, str) and "." in t:
                    head, _, tail = t.partition(".")
                    err("toolface_dot_form",
                        f"岗位 '{a.get('id')}' 的工具名 `{t}` 是点号写法；schema 是两层 map——"
                        f"应写 `resources.mcpTools['{head}'] = ['{tail}']`"
                        f"（本仓 370 处 allowTools 全是裸名，点号写法既不注册也不被校验）",
                        f"agents[{a.get('id')}].resources.mcpTools['{srv}']")

    for s in (manifest.get("mcpServers") or []):
        if not isinstance(s, dict):
            continue
        for t in (s.get("allowTools") or []):
            if isinstance(t, str) and "." in t:
                err("toolface_dot_form",
                    f"包级 `mcpServers[{s.get('registryName')}].allowTools` 里的 `{t}` 是点号写法"
                    f"——该列表只收裸工具名，服务由 `registryName` 表达",
                    f"mcpServers[{s.get('registryName')}].allowTools")
    return errs


_CJK_NUM = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_EN_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
           "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9}

# 段数：蓝图草稿是九段（`blueprints/schema.md`，META-LINE §4.0 逐段列名），
# 提案单位是八段（META-LINE §4）。六段/七段没有任何一处现行形状对应它们。
_SEG_PATTERNS = (
    re.compile(r"([六七八九十])段"),
    re.compile(r"\b(six|seven|eight|nine)[- ]segment"),
    re.compile(r"\b(seis|siete|ocho|nueve)\s+segmentos\b", re.I),
    re.compile(r"\b(six|sept|huit|neuf)\s+segments\b", re.I),
)
# 岗数：**编制陈述**的字面形态才判，不判「另两岗」「架构/技能两岗持提审权」这类
# 相对计数与历史叙述（首版按裸 `N岗` 判，在本包上直接假红 7 处，其中 4 处是在数别人或记账）。
_POST_PATTERNS = (
    re.compile(r"([两二三四五])岗(共编|共同|编制|协同|共用|并编|同编|产出)"),
    # 拉丁文只认「N-post + 编制类名词」：裸 `Four-post handoff contract` 讲的是交接链上的四岗
    # （marketing 那份舰队是五岗、交接链是四岗，两句话都对），按裸 -post 判会假红。
    re.compile(r"\b(two|three|four|five)[- ]posts?\s+"
               r"(fleet|team|lineup|construction|setup|share|co-?work|collaborat)", re.I),
    re.compile(r"([二三四五])崗(共同|で一つ)"),
    re.compile(r"\b(dos|tres|cuatro|cinco)\s+puestos,"),
    re.compile(r"\b(deux|trois|quatre|cinq)\s+postes,"),
)


def _iter_instruction_texts(manifest: dict):
    """岗**真正据以行事**的表面，以及会被读到的展示面 —— 逐条给 (路径, 文本)。

    为什么必须扫这么宽：2026-11-14 修「六段」时，同一个过时事实同时活在**五处**——
    架构师 persona、promptTemplate（agents 与 fleet.members 各一份）、两份技能正文、
    以及四份语种覆盖层（en/ja/es/fr 全写 six segments / 六段）。只改 persona 等于
    留下第二份真源，而下一次改的人只会看到自己眼前那一份。
    """
    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        yield f"agents[{aid}].persona", a.get("persona") or ""
        yield f"agents[{aid}].cadence.promptTemplate", ((a.get("cadence") or {}).get("promptTemplate") or "")
    for m in (manifest.get("fleet") or {}).get("members") or []:
        if isinstance(m, dict):
            yield (f"fleet.members[{m.get('ref')}].cadence.promptTemplate",
                   ((m.get("cadence") or {}).get("promptTemplate") or ""))
    for s in manifest.get("skills") or []:
        if isinstance(s, dict):
            yield f"skills[{s.get('name')}].description", s.get("description") or ""
            yield f"skills[{s.get('name')}].content", s.get("content") or ""
    for k in ("description", "notes"):
        yield k, manifest.get(k) or ""

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            yield path, node

    yield from walk(manifest.get("i18n") or {}, "i18n")


def check_stated_counts(name: str, manifest: dict) -> list[dict]:
    """M21 段数与岗数的**陈述**必须与现行形状一致（跨 persona / prompt / 技能正文 / i18n）。

    抓的是「包自己在说明书里把形状说错了」。它不是文字洁癖：模型据以行事的正是这些句子，
    说「六段」而契约是九段时，它会按六段收工（2026-11-14 实况：架构师 prompt 同一句里
    前半「六段」后半「九段齐后」，两份技能正文与四份语种覆盖层全写 six segments / 六段）；
    说「两岗」而编制是三岗时，市场/面板上的介绍与实际编制不符。

    判据只认**能对上的数字**，不做语义推断：
      · 段数出现 6 / 7 ⇒ `stale_segment_count`（没有任何现行形状是六段或七段）；
      · 段数出现 8 而同一段文本里没有「发布对象 / release object」⇒ `ambiguous_segment_count`
        （八段只指发布对象；裸写八段会被读成草稿段数）；
      · 岗数与 `len(fleet.members)` 不等 ⇒ `stale_headcount`。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str) -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    members = [m for m in ((manifest.get("fleet") or {}).get("members") or []) if isinstance(m, dict)]
    headcount = len(members)
    # 没有 `fleet.members` 的包（角色包/技能包，实测 `erp/supply-chain-recon-roles` 就是这种）
    # 不声明编制 ⇒ 岗数陈述无从比对，**判不出**，不许当红（缺数据不是命中）。

    for entry, text in _iter_instruction_texts(manifest):
        if not text:
            continue
        for pat in _SEG_PATTERNS:
            for m in pat.finditer(text):
                tok = m.group(1)
                n = _CJK_NUM.get(tok) or _EN_NUM.get(tok.lower())
                if n in (6, 7):
                    err("stale_segment_count",
                        f"{entry} 把草稿写成「{m.group(0)}」——草稿是**九段**"
                        "（boundary/dataSources/fleet/agents/mcpServers/board/monitoring/"
                        "acceptance/i18n，`blueprints/schema.md`）；六段是 2026-11-08 之前的旧形状，"
                        "模型会照它收工（2026-11-14 实况：同一句前半「六段」后半「九段齐后」）", entry)
                elif n == 8:
                    ctx = text
                    if "发布对象" not in ctx and "release object" not in ctx.lower():
                        err("ambiguous_segment_count",
                            f"{entry} 出现裸写的「{m.group(0)}」——八段只指**发布对象**"
                            "（manifest/org/memory/toolkit/dashboard/acceptance/evidence/cost）；"
                            "草稿是九段。同一段文本里没提「发布对象」时，这句话会被读成草稿段数", entry)
        for pat in (_POST_PATTERNS if headcount else ()):
            for m in pat.finditer(text):
                tok = m.group(1)
                n = _CJK_NUM.get(tok) or _EN_NUM.get(tok.lower())
                if n is not None and n != headcount:
                    err("stale_headcount",
                        f"{entry} 说「{m.group(0)}」，而 `fleet.members` 实际是 {headcount} 个岗"
                        f"（{[x.get('ref') for x in members]}）——编制变了而说明书没跟着变，"
                        "市场与面板上的介绍会与真实编制不符", entry)
    return errs


def check_cadence_copies(name: str, manifest: dict) -> list[dict]:
    """M22 同一岗的 `promptTemplate` 在 `agents[]` 与 `fleet.members[]` 里必须**逐字相同**。

    真源是两份：包 schema 允许两处都写 cadence（`agents[]` 描述这个岗的节律，`fleet.members[]`
    描述它在队里的排期），而**平台跑的是 members 那份**。两处并存且无人比对 ⇒ 改一处、
    另一处看起来仍然存在，于是「改了没生效」和「改对了」在文件里长得一模一样。

    2026-11-14 实历，而且**是我自己造成的**：给装配岗加「由本岗提审」那句时只改了 `agents[]`，
    `fleet.members[]` 仍是旧文（1174 vs 1091 字符）⇒ 线上那轮跑的还是没有提审职责的旧指令；
    而当时的核对是「线上 vs 包（members）」，两边都是旧的，所以**核对通过、缺陷还在**。
    同一次还有反向的一例：架构师两份各有对方没有的一句（一份有校验口径、一份有命名纪律）。

    判据：按 ref 对位，两份都非空且不相等 ⇒ 红；只写一处 ⇒ 不判（平台按 members 跑，另一处
    可省略）；`turnTimeoutMs` 两份都有且不等也红（同族：生效的是 members 那份）。

    **适用范围＝元线三包**，不要拿它扫全仓：本闸本来就只跑 `META_PACKS`，而 M11 已经把
    「两份该一致」当作元线的既有约定（成员预算与服务岗预算不一致即红）。仓里另有别的体系——
    `shared/infra-factory-fleet` 的 `agents[]` 存的是模板草稿（正文写着「错峰与在册舰队时刻表
    待 fleet_list 实探终校」）、`fleet.members[]` 才是逐岗真指令，三岗两份都「不相等」且**都对**；
    把它推广成全仓判据会把这一族整类判红（同族于「判据把一类合法部署判红」）。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str) -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    agents = {a.get("id"): a for a in (manifest.get("agents") or []) if isinstance(a, dict)}
    for m in (manifest.get("fleet") or {}).get("members") or []:
        if not isinstance(m, dict):
            continue
        ref = m.get("ref")
        a = agents.get(ref)
        if not a:
            continue
        ac = a.get("cadence") or {}
        mc = m.get("cadence") or {}
        ap, mp = ac.get("promptTemplate"), mc.get("promptTemplate")
        if isinstance(ap, str) and isinstance(mp, str) and ap and mp and ap != mp:
            err("cadence_copy_diverges",
                f"岗 '{ref}' 的 promptTemplate 在 `agents[]`({len(ap)} 字) 与 `fleet.members[]`({len(mp)} 字) "
                "不一致——**平台跑的是 members 那份**，所以改了 agents 那份等于没改，而两处都「看起来在」"
                "（2026-11-14 实况：装配岗的提审职责只写进 agents，线上跑的仍是没有它的旧文；"
                "当时的核对「线上 vs members」两边都旧，因此通过而缺陷仍在）。两份必须逐字相同",
                f"agents[{ref}].cadence.promptTemplate ↔ fleet.members[{ref}].cadence.promptTemplate")
        at, mt = ac.get("turnTimeoutMs"), mc.get("turnTimeoutMs")
        if at is not None and mt is not None and at != mt:
            err("cadence_copy_diverges",
                f"岗 '{ref}' 的 turnTimeoutMs 两份不等（agents {at} / members {mt}）——生效的是 members 那份",
                f"agents[{ref}].cadence.turnTimeoutMs")
    return errs


# ── M23 的词表 ────────────────────────────────────────────────────────────
# 只认**工作区**里的小账（`workspace/*.md`）；`blueprints/schema.md` 这类仓内契约不提。
_LEDGER_PATH_RE = re.compile(r"workspace/([A-Za-z0-9_.-]+\.md)")
# 分段切到「一句一动作」那么细：同一段里既有「只读 A」又有「不要读 B」是常态，
# 按整句判会把否定与肯定混进一段（2026-11-15 首版实测：技能作者那句里 skills-pending
# 与「不要读整份 blueprint-draft」落在同一段，否定词把两条一起洗掉了）。
_LEDGER_SEG_SPLIT = re.compile(r"[。；;！？\n：:—]+")
_LEDGER_PRODUCE = ("写", "落盘", "产出", "重建", "新建", "更新")
_LEDGER_CONSUME = ("只读", "读取", "读", "拿到", "依据")
_LEDGER_NEGATION = ("不要", "别", "禁", "不读", "不写", "而非")
# 「不在了怎么办」的措辞。注意「不存在」不是否定句，恰是重建条款的信号。
_LEDGER_REBUILD = ("重建", "不存在", "丢了", "丢失", "缺失", "没生成", "为空")


def _ledger_segments(text: str) -> list[str]:
    return [s for s in _LEDGER_SEG_SPLIT.split(text or "") if s.strip()]


def _post_corpora(manifest: dict) -> list[tuple[str, str]]:
    """(ref, 这个岗**实际会跑**的正文) = persona + members 那份 promptTemplate。

    取 members 那份的理由与 M22 同源：平台按 `fleet.members[].cadence.promptTemplate` 跑。
    """
    personas: dict[str, str] = {}
    prompts: dict[str, str] = {}
    for a in manifest.get("agents") or []:
        if isinstance(a, dict) and a.get("id"):
            personas[a["id"]] = a.get("persona") or ""
            prompts[a["id"]] = ((a.get("cadence") or {}).get("promptTemplate") or "")
    for m in (manifest.get("fleet") or {}).get("members") or []:
        if isinstance(m, dict) and m.get("ref") in prompts:
            mt = (m.get("cadence") or {}).get("promptTemplate") or ""
            if mt:
                prompts[m["ref"]] = mt
    return [(ref, personas.get(ref, "") + "\n" + prompts.get(ref, "")) for ref in prompts]


def check_handoff_ledgers(name: str, manifest: dict) -> list[dict]:
    """M23 小交接账：**写侧必须教重建、读侧必须真有人写**。

    抓的是「A 岗写一份小账 → B 岗只读它」这种编制里两个方向的静默失效：

      ① 写侧没写「不在了怎么办」。一次没触发、一次被删，链条就永久断掉，而**每一次报数
         都读起来像已经交接完**。2026-11-15 实况：架构师那轮 `success:true`、正文复述的
         九段枚举全对，报数写着「技能作者岗已交接 `skills-pending.md`（1 新写 + 1 复用）」，
         而 `ls` 结果两份小账**都不存在** ⇒ 技能作者下一轮仍会退回读 107 KiB 整稿，
         修好的洞原封不动。（同族教训：报数行是模型对自己行为的叙述，不是对文件系统的断言。）
      ② 读侧指着一份没人被要求写的文件。它会读到空、然后静默退回读大件——症状与
         「小账机制根本没建」完全一样，而两侧 prompt 各自都自洽。

    判据（只断言，不推断）：判据落在**交接链**上，不落在单个岗上——
      · 某岗正文出现「读 `workspace/X.md`」（否定句不算）而整条链上（任一写者或读方）
        都没人写「它不在了怎么重建」（重建/不存在/丢了/…同段提到 `X.md`）⇒ 红；
      · 有写侧的链缺重建条款 ⇒ `handoff_ledger_no_rebuild`；没有写侧的读 ⇒
        `handoff_ledger_unreadable`。
      · 只写没人读不判（可能是给主人看的账）；共写工作文档（blueprint-draft 三岗追加）
        不要求每个写者都会重建——链上有一方会重建就有恢复路。

    适用范围＝元线三包（`META_PACKS`）。全仓目前只有 `shared/harness-constructor` 引用
    `workspace/*.md`——这条判据今天只对这一个小账编制生效，**不推广**：别的体系
    （如 `infra-factory-fleet`）的小账名与职责分布不同，硬套会把合法写法判红。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str) -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    corpora = _post_corpora(manifest)
    if not corpora:
        return errs

    produced: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    for ref, text in corpora:
        for seg in _ledger_segments(text):
            # 动词只认**路径之前**的：中文语序动词在前，而「（架构师**写**的交接单）」
            # 这种路径后的括注是描述、不是承诺——2026-11-15 首版把它算成写侧，
            # ghost 臂（只改读侧文件名）就再也红不出「没有任何岗被要求写它」。
            for m in _LEDGER_PATH_RE.finditer(seg):
                fn = m.group(1)
                pre = seg[:m.start()]
                negated = any(k in seg for k in _LEDGER_NEGATION)
                if any(k in pre for k in _LEDGER_PRODUCE):
                    produced.setdefault(fn, set()).add(ref)
                if (not negated) and any(k in pre for k in _LEDGER_CONSUME):
                    consumed.setdefault(fn, set()).add(ref)

    corpus_of = dict(corpora)

    def has_rebuild(ref: str, fn: str) -> bool:
        return any(fn in seg and any(k in seg for k in _LEDGER_REBUILD)
                   for seg in _ledger_segments(corpus_of[ref]))

    # 判据落在「交接链」上，不落在单个岗上：一份共写的工作文档（如 blueprint-draft，
    # 三岗追加）不该要求**每个**写者都会重建——只要链上任一方（任一写者或读方）教了
    # 「不在了怎么重建」，链条就有一条恢复路。反过来，交接链上**没有任何一方**写这句
    # 才是 2026-11-15 实历的形状：写侧被要求产出、读侧被要求只读，双方都没说文件
    # 不在了怎么办 ⇒ 一次没触发就永久断，且每轮报数读起来都像已经交接完。
    for fn, c_refs in sorted(consumed.items()):
        producers = sorted(produced.get(fn, set()))
        for ref in sorted(c_refs):
            if has_rebuild(ref, fn) or any(has_rebuild(p, fn) for p in producers):
                continue
            if producers:
                err("handoff_ledger_no_rebuild",
                    f"小交接账 `workspace/{fn}`：写侧（{'、'.join(producers)}）与读侧（'{ref}'）"
                    "都没有「不存在就重建」的条款——交接链上没有任何一方写了「它不在了怎么办」；"
                    "一次没触发、一次被删，下游岗就永久退回读整份大件，而每轮报数读起来都像"
                    "已经交接完（2026-11-15 实况：报数写着「已交接 `skills-pending.md`」，"
                    "盘上两份小账都不存在）",
                    f"agents[{ref}] / fleet.members[{ref}].cadence.promptTemplate")
            else:
                err("handoff_ledger_unreadable",
                    f"岗 '{ref}' 被要求读 `workspace/{fn}`，但没有任何岗被要求写它、"
                    "本岗也没被教怎么重建——交接链上没有任何一方写了「它不在了怎么办」；"
                    "它会读到空、然后静默退回读整份大件，症状与「小账根本没建」一模一样，"
                    "而两侧 prompt 各自都自洽",
                    f"agents[{ref}].cadence.promptTemplate")
    return errs


def check_fleet_locator(name: str, manifest: dict) -> list[dict]:
    """M14 要按舰队**读回合**的岗，必须能查到舰队清单。

    `fleet_turns_read` / `fleet_spec_read` 的 `fleetId` 是**必填**，而唯一正路是
    `fleet_list` 拿 id。缺了它，岗只能自己想办法：2026-11-14 实测审计岗 31 步全花在
    翻工作区（`ls`/`glob`/`sqlite3`/`grep *.md`）找自己舰队 id，再对 14 支舰队逐个试读，
    最后 720s soft stop、零裁决零报数。这类「工具面缺一块，模型就自己发明取数通道」的形态
    只能靠判据挡，不能靠叮嘱。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        if not ((a.get("cadence") or {}).get("cron")):
            continue
        tools = set()
        for _ref, lst in ((a.get("resources") or {}).get("mcpTools") or {}).items():
            if isinstance(lst, list):
                tools |= set(lst)
        # 只对 `fleet_turns_read` 要求 fleet_list：读回合是**探索性**的（不知道该读哪支），
        # 而 fleet_spec_read/fleet_draft 的 fleetId 可以由上游 handoff 带进来（技能作者就是
        # 这种形态）。第一版把三者都算进去，把构造师的技能作者判红了——那是把「能拿 id 的
        # 两条路」混成一条，等于用一条过宽的判据制造假红。
        needs_fleet = {"fleet_turns_read"} & tools
        if needs_fleet and "fleet_list" not in tools:
            err("fleet_locator_missing",
                f"岗位 '{aid}' 要用 {sorted(needs_fleet)}（`fleetId` 必填）却没给 `fleet_list`"
                "——它只能去翻工作区/数据库猜 fleetId（实测 31 步烧光预算、零产出）。"
                "把 `fleet_list` 加进该岗的 mcpTools",
                f"agents[{aid}].resources.mcpTools")
    return errs


def check_report_tool_face(name: str, manifest: dict) -> list[dict]:
    """M13 有 cron 的岗必须真的拿到 `report_status`。

    2026-11-14 实测（评测岗首轮）：它跑完了、也有结论，却一条报数都没落。轨迹里两次
    `tool_search` 的回包是「No effective tool matches "report"」——**这个会话里没有报数工具**。

    口径在 `lib/pack-manifest.js` 的 `effectiveMcpTools()`：一个包若声明了 `fleet` 这个
    server，就按 `allowTools ∩ role.mcpTools` 裁剪；**没声明 `fleet` 的包则完全不受限**
    （全量工具面）。所以「声明了 fleet 却只允许 auditor_submit_verdict」＝亲手把报数口关掉，
    而这一步是静默的：包校验绿、舰队建得起来、岗上也 on_duty。
    反面证据同时存在：优化团队的 `mo-inventory` 没声明 `fleet`，它成功报过数。

    判据（只对**有 cadence 的岗**成立——被动岗不报数）：
      若包级声明了 `fleet`，则该岗经 `effectiveMcpTools` 算出的 fleet 工具面里必须有
      `report_status`；否则该岗永远报不出数。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    servers = {s.get("registryName"): s for s in (manifest.get("mcpServers") or [])
               if isinstance(s, dict) and s.get("registryName")}
    fleet = servers.get("fleet")
    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        cron = ((a.get("cadence") or {}).get("cron"))
        if not cron:
            continue  # 被动岗不报数
        declared = ((a.get("resources") or {}).get("mcpServers"))
        if isinstance(declared, list) and "fleet" not in declared:
            err("report_tool_excluded",
                f"岗位 '{aid}' 有 cron（{cron}）但 resources.mcpServers 里没有 'fleet'"
                "——它的工具面会被这一层裁掉，报数口随之消失（真机表现为 tool_search 搜不到 report）",
                f"agents[{aid}].resources.mcpServers")
            continue
        role = (((a.get("resources") or {}).get("mcpTools")) or {})
        role_fleet = role.get("fleet") if isinstance(role.get("fleet"), list) else None
        if fleet is not None:
            pack_list = fleet.get("allowTools") if isinstance(fleet.get("allowTools"), list) else None
            effective = (role_fleet if role_fleet is not None else pack_list)
            if pack_list is not None and role_fleet is not None:
                effective = [x for x in role_fleet if x in pack_list]
            if effective is not None and "report_status" not in effective:
                err("report_tool_filtered",
                    f"岗位 '{aid}' 有 cron（{cron}），但 `fleet` 工具面经裁剪后是 {effective}，"
                    "里面没有 `report_status`——这个岗永远报不出数"
                    "（包级 allowTools 与岗位 mcpTools 取交集，交集里没有就等于没有）。"
                    "把 report_status 同时写进包级 allowTools 与岗位 mcpTools",
                    f"agents[{aid}].resources.mcpTools.fleet")
    return errs


def check_batch_agreement(name: str, manifest: dict) -> list[dict]:
    """M12 同一岗的两份正文对「本轮做几件」必须给同一个数。

    为什么：`persona` 是给人读的、`cadence.promptTemplate` 是**真正驱动那一轮**的。
    2026-11-14 我把 persona 的分批上限改成「只对拍 1 支」，promptTemplate 里还写着
    「至多 3 支」——真跑的那一轮按 3 支走（实测读了 3 支、644s）。两份正文矛盾时，
    生效的永远是 promptTemplate，而人看的是 persona：查「为什么它读了 3 支」会先怀疑模型
    不听指令，实际是自己给了两条不同的指令。

    判据只认「至多 N 支 / 至多 N 条 / 至多 N 个」这一种定量措辞（两份都出现时才比对），
    定性描述不参与——避免把「分批」这类泛泛说法判成不一致。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        persona = a.get("persona") or ""
        prompt = (a.get("cadence") or {}).get("promptTemplate") or ""
        pn = _batch_cap(persona)
        qn = _batch_cap(prompt)
        if pn and qn and pn.group(1) != qn.group(1):
            err("batch_cap_mismatch",
                f"岗位 '{aid}' 的分批上限两份不一致：persona 写「{pn.group(0)}」、"
                f"promptTemplate 写「{qn.group(0)}」——**真正驱动那一轮的是 promptTemplate**，"
                "人读的是 persona；两份矛盾时查『它为什么不听话』会先怪模型",
                f"agents[{aid}].cadence.promptTemplate")
    return errs


def check_turn_budget(name: str, manifest: dict) -> list[dict]:
    """M11 每岗回合预算必须在平台可接受区间内，且只能写在真生效的那一处。

    平台真源：`server/cron-runner.js` 的 `resolveTurnBudgetMs()` —— 读
    `member.cadence.turnTimeoutMs`（缺省 10 分钟），**夹在 [2min, 30min]**；边的
    `executionPolicy.timeoutMinutes` 优先。夹紧不报错、不告警：写 45 分钟实际是 30 分钟，
    写 30 秒实际是 2 分钟。包里那行看起来是作者想要的，线上跑的是别的值。

    这也是 2026-11-14 评测岗撞墙的处置落点：一轮读取固定要 6-8 步，默认 10 分钟（soft stop
    480s）不够，而「把工作量做小」是并行的另一半——两边都要能写进包，否则抬预算就只是
    线上单点设置，包又不描述线上了。
    """
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    fl = manifest.get("fleet")
    if not isinstance(fl, dict):
        return errs
    agent_cad = {a.get("id"): (a.get("cadence") or {}) for a in (manifest.get("agents") or [])
                 if isinstance(a, dict)}
    for m in fl.get("members") or []:
        if not isinstance(m, dict):
            continue
        ref = m.get("ref")
        cad = m.get("cadence") or {}
        val = cad.get("turnTimeoutMs")
        if val is None:
            continue
        try:
            ms = int(val)
        except (TypeError, ValueError):
            err("turn_budget_invalid", f"岗位 '{ref}' 的 `turnTimeoutMs` 不是整数：{val!r}",
                f"fleet.members[{ref}].cadence.turnTimeoutMs")
            continue
        if ms < TURN_BUDGET_MIN_MS or ms > TURN_BUDGET_MAX_MS:
            err("turn_budget_out_of_range",
                f"岗位 '{ref}' 的 `turnTimeoutMs`={ms} 出了平台区间 "
                f"[{TURN_BUDGET_MIN_MS}, {TURN_BUDGET_MAX_MS}]——平台会**静默夹紧**"
                f"（写到 {max(TURN_BUDGET_MIN_MS, min(ms, TURN_BUDGET_MAX_MS))}），"
                "包里这行字看着没变但线上跑的是另一个值",
                f"fleet.members[{ref}].cadence.turnTimeoutMs")
        peer = agent_cad.get(ref) or {}
        if "turnTimeoutMs" in peer and peer.get("turnTimeoutMs") != val:
            err("turn_budget_mismatch",
                f"岗位 '{ref}' 的成员预算 {val} 与同 ref 的 agent.cadence {peer['turnTimeoutMs']} "
                "不一致——生效的是成员那份，人读的是 agent 那份（同 promptTemplate 的坑）",
                f"agents[{ref}].cadence.turnTimeoutMs")
    return errs


def check_report_caliber(name: str, agents: list) -> list[dict]:
    """报数口径：只查一种已被证伪的说法，且允许「引用旧口径来反驳它」。

    为什么单独一条函数：这条判据要同时作用于**三份 manifest 与蓝图**，而两边的顶层形状
    不同（蓝图没有 `memory`/`toolkit` 段），把整份文档塞进 check_pack 会连 M1/M3 一起误报。
    """
    errs: list[dict] = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        text = (a.get("persona") or "") + "\n" + ((a.get("cadence") or {}).get("promptTemplate") or "")
        for m in PER_TURN_REPORT.finditer(text):
            window = text[max(0, m.start() - 150): m.end() + 150]
            if any(k in window for k in CALIBER_REFUTED):
                continue  # 在引用旧口径来反驳，或在讲 720 口径——不是错
            errs.append({"pack": name, "code": "report_caliber_per_turn",
                         "error": (f"岗位 '{aid}' 把报数当成每回合的额度——原文「{m.group(0)}」。"
                                   f"平台口径是 3 条 / 720 分钟（按岗、跨轮共享）：一轮首条豁免，"
                                   f"第二条起按窗口计数，写成每回合 {m.group(1)} 次会让这个岗在 "
                                   f"1.5 轮内烧光 12 小时额度。改成「一轮只报一次、明细写进同一条」"),
                         "entry": f"agents[{aid}].persona"})
    return errs


def check_pack(name: str, path: Path, manifest: dict | None = None) -> list[dict]:
    """返回该包的错误清单（空 = 通过）。"""
    errs: list[dict] = []

    def err(code: str, msg: str, entry: str = "") -> None:
        errs.append({"pack": name, "code": code, "error": msg, "entry": entry})

    if manifest is None:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            err("pack_unreadable", f"{path} 读不到/解析不了：{type(e).__name__}: {e}")
            return errs

    agents = manifest.get("agents") if isinstance(manifest.get("agents"), list) else []

    # ── M1 memory 四轴 + 逐岗 guidance ────────────────────────────────────
    mem = manifest.get("memory")
    if not isinstance(mem, dict):
        err("memory_missing", "缺包级 `memory` 段（四轴 store/read/write/process）", "memory")
    else:
        missing = sorted(MEMORY_AXES - set(mem))
        if missing:
            err("memory_axes_missing", f"`memory` 缺轴：{missing}", "memory")
    for a in agents:
        if not isinstance(a, dict):
            continue
        g = ((a.get("memory") or {}).get("guidance")
             if isinstance(a.get("memory"), dict) else None)
        if not isinstance(g, str) or not g.strip():
            err("guidance_missing", f"岗位 '{a.get('id')}' 缺 `memory.guidance`",
                f"agents[{a.get('id')}].memory.guidance")
        elif len(g) > GUIDANCE_MAX:
            err("guidance_too_long",
                f"岗位 '{a.get('id')}' 的 guidance 超 {GUIDANCE_MAX} 字（{len(g)}）",
                f"agents[{a.get('id')}].memory.guidance")

    # ── M2 逐岗工具面 ────────────────────────────────────────────────────
    for a in agents:
        if not isinstance(a, dict):
            continue
        mt = ((a.get("resources") or {}).get("mcpTools")
              if isinstance(a.get("resources"), dict) else None)
        if not isinstance(mt, dict) or not mt:
            err("toolface_missing",
                f"岗位 '{a.get('id')}' 缺 `resources.mcpTools`（逐岗工具面不得省略）",
                f"agents[{a.get('id')}].resources.mcpTools")
        elif not any(isinstance(v, list) and v for v in mt.values()):
            err("toolface_empty",
                f"岗位 '{a.get('id')}' 的 `resources.mcpTools` 全为空列表",
                f"agents[{a.get('id')}].resources.mcpTools")

    # ── M3 看板 ──────────────────────────────────────────────────────────
    board = manifest.get("board")
    if not isinstance(board, list) or not board:
        err("board_missing", "缺 `board` 段（看板骨架不得省略；无内容写显式空值也要有段）",
            "board")

    # ── M4 写面禁令 ──────────────────────────────────────────────────────
    for s in (manifest.get("mcpServers") or []):
        if not isinstance(s, dict):
            continue
        for t in (s.get("allowTools") or []):
            if not isinstance(t, str):
                continue
            if t in FORBIDDEN_TOOLS or any(sub in t for sub in FORBIDDEN_SUBSTRINGS):
                err("forbidden_tool",
                    f"工具面含元线禁用工具 '{t}'（发布/实例化/启停只能走 propose_action）",
                    f"mcpServers[{s.get('registryName')}].allowTools")
    for a in agents:
        if not isinstance(a, dict):
            continue
        mt = ((a.get("resources") or {}).get("mcpTools") or {}) if isinstance(a.get("resources"), dict) else {}
        for server, tools in mt.items():
            for t in (tools or []):
                if isinstance(t, str) and (t in FORBIDDEN_TOOLS
                                           or any(sub in t for sub in FORBIDDEN_SUBSTRINGS)):
                    err("forbidden_tool",
                        f"岗位 '{a.get('id')}' 的工具面含禁用工具 '{t}'",
                        f"agents[{a.get('id')}].resources.mcpTools[{server}]")

    # ── M5 节奏错峰 ──────────────────────────────────────────────────────
    clocks = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        cad = a.get("cadence") or {}
        mins = _cron_minutes(cad.get("cron"))
        if mins is None:
            err("cadence_unparsable",
                f"岗位 '{a.get('id')}' 的 cron 无法解析：{cad.get('cron')!r}",
                f"agents[{a.get('id')}].cadence.cron")
            continue
        clocks.append((a.get("id"), mins, cad.get("tz")))
    clocks.sort(key=lambda x: x[1])
    for (a1, m1, tz1), (a2, m2, tz2) in zip(clocks, clocks[1:]):
        if tz1 != tz2:
            err("cadence_tz_mismatch", f"'{a1}' 与 '{a2}' 的时区不一致（{tz1} vs {tz2}）", "cadence")
        gap = m2 - m1
        if gap < MIN_GAP_MINUTES:
            err("cadence_too_close",
                f"'{a1}' 与 '{a2}' 间隔 {gap} 分钟 < {MIN_GAP_MINUTES} 分钟（同 cell 串行队列会互相挤）",
                "cadence")

    # ── M6 persona 不得含模板变量 ──────────────────────────────────────────
    # 2026-11-13 真机实测：在 hc-assembler 的 persona 里写了 `{{fleetContext}}`，
    # 回合在第一秒就失败——malformed prompt variable reference … in section
    # "deployment:persona-prefix"。persona 是要被渲染的模板，正文里出现 `{{`
    # 不是文字问题而是部署期崩溃，所以必须在发布前拦，而不是等真机跑。
    for a in agents:
        if not isinstance(a, dict):
            continue
        persona = a.get("persona") or ""
        if "{{" in persona:
            frag = persona[persona.index("{{"):persona.index("{{") + 40]
            err("persona_template_variable",
                f"岗位 '{a.get('id')}' 的 persona 含模板变量 {frag!r}"
                "——persona 会被当模板渲染，部署期即失败；要提集群上下文就写中文描述，别写变量名",
                f"agents[{a.get('id')}].persona")

    # ── M7 报数配额（平台 FLOOD_MAX_PER_WINDOW=3 / 720 分钟，按岗） ─────────
    # 两条判据分开，因为它们的失败形状不同：
    #   ① 措辞判据：同一岗一轮内既要求「首报」又要求「末报」⇒ 一轮两条。
    #      2026-11-13 真机实测 turn 1631 第二条被拒：report limit reached (3 per 720 minutes)。
    #      这一条**永远是硬的**：豁免只放行同一轮的第一条，第二条起仍按窗口计数。
    #   ② 节奏判据：同一岗在任一 12h 窗口内点火 >3 次。**它现在不是平台边界**——2026-11-13 豁免
    #      修好并上产后（判据改成回合起点，镜像 sha-abb7e39 起），每轮首条都放行，点火 4 次也照报。
    #      保留下这条是因为「一天 4 轮 × 每轮一条」会让这个岗的 token 与可读性都堆在一个窗口里，
    #      属本线自律；报错文案因此不再写「必被拒」，只写「本线不把一天的量堆进一个窗口」。
    # 判据②按**岗**算，不按舰队：三个岗各一轮 ≠ 一个岗三轮。按舰队数轮次去比按岗配额＝拿邻近信号冒充判据。
    for a in agents:
        if not isinstance(a, dict):
            continue
        aid = a.get("id")
        text = (a.get("persona") or "") + "\n" + ((a.get("cadence") or {}).get("promptTemplate") or "")
        first = [h for h in FIRST_REPORT_HINTS if h in text]
        last = [h for h in LAST_REPORT_HINTS if h in text]
        banned = any(h in text for h in REPORT_BAN_HINTS)
        if first and last and not banned:
            err("report_twice_per_turn",
                f"岗位 '{aid}' 同时要求「{first[0]}」与「{last[0]}」＝一轮报两次；"
                f"平台配额 {REPORT_QUOTA} 条/{REPORT_WINDOW_MIN} 分钟（按岗），1.5 轮即被拒。"
                "改成「一轮只报一次、明细写进同一条」，并写明被拒时改落工作区台账",
                f"agents[{aid}]")

        cron = (a.get("cadence") or {}).get("cron")
        runs = _runs_per_window(cron, REPORT_WINDOW_MIN)
        if runs is None:
            err("report_quota_unjudgeable",
                f"岗位 '{aid}' 的 cron {cron!r} 无法换算点火次数——"
                f"报数配额 {REPORT_QUOTA} 条/{REPORT_WINDOW_MIN} 分钟按岗计，判不了就不能算过",
                f"agents[{aid}].cadence.cron")
        elif runs > REPORT_QUOTA:
            err("report_quota_exceeded",
                f"岗位 '{aid}' 的 cron {cron!r} 在 {REPORT_WINDOW_MIN} 分钟内点 {runs} 次 > "
                f"本线上限 {REPORT_QUOTA} 次——这不是平台会拒（每轮首条已豁免），"
                f"而是不让一个岗把一天的量堆进同一个窗口；"
                "要加密节奏就拆成新岗（新岗新配额），不要把一个岗堆在同一窗口",
                f"agents[{aid}].cadence.cron")

    # M7 之三：已证伪的「每回合 N 次」口径（同一条判据也作用于蓝图，见 run()）
    errs.extend(check_report_caliber(name, agents))

    # M8：未解析的 pack-id 占位符不许活到发布产物里
    errs.extend(check_placeholders(name, manifest))

    # M9：编排边自洽（端点存在、trigger 时刻＝岗位节律）
    errs.extend(check_member_triggers(name, manifest))

    # M10：persona 承诺的能力必须真的在工具面里（评测岗「SQL 真值」而工具面没有 SQL）
    errs.extend(check_capability_executable(name, manifest))

    # M11：回合预算必须落在平台区间，且成员与服务岗两份一致（生效的是成员那份）
    errs.extend(check_turn_budget(name, manifest))

    # M12：persona（人读）与 promptTemplate（机器跑）的分批上限必须同一个数
    errs.extend(check_batch_agreement(name, manifest))

    # M13：有 cron 的岗必须真的拿到报数工具（fleet 工具面裁剪不得把 report_status 筛掉）
    errs.extend(check_report_tool_face(name, manifest))

    # M14：要按舰队取数的岗必须能列舰队（否则它会自己发明取数通道）
    errs.extend(check_fleet_locator(name, manifest))

    # M15：有 cron 的岗必须有一个收口得了的结论通道（auditor_submit_verdict 要待决事项）
    errs.extend(check_verdict_channel(name, manifest))

    # M16：正文承诺的报数槽必须在 kpiSlots 里声明过（未声明的键静默进 details、不上卡片）
    errs.extend(check_declared_slots(name, manifest))

    # M17：正文承诺的写面必须工具面里有（缺读面绕得过去，缺写面只能压缩到超时）
    errs.extend(check_write_channel(name, manifest))

    # M18：逐队拉回合史的岗必须显式声明预算（缺省 600s 的软停 480s < 实测 438-531s）
    errs.extend(check_heavy_read_budget(name, manifest))

    # M19：四语种槽位覆盖层必须全译或整省（partial overlay 被平台校验器拒收）
    errs.extend(check_i18n_slots(name, manifest))

    # M20：逐岗工具面的形状（服务须包级声明；工具名只收裸名，点号写法会被静默丢弃）
    errs.extend(check_toolface_shape(name, manifest))

    # M21：段数与岗数的**陈述**必须与现行形状一致（五处表面：persona/prompt/技能正文/i18n）
    errs.extend(check_stated_counts(name, manifest))

    # M22：同一岗的 promptTemplate 在 agents[] 与 fleet.members[] 两份里必须逐字相同
    errs.extend(check_cadence_copies(name, manifest))

    # M23：小交接账——写侧必须教「不在了怎么重建」，读侧指的文件必须真有人被要求写
    errs.extend(check_handoff_ledgers(name, manifest))

    return errs


def run(packs: dict[str, Path] | None = None) -> tuple[list[dict], list[str]]:
    """返回 (错误清单, 读不到的包)。"""
    packs = packs or {k: REPO / v for k, v in META_PACKS.items()}
    errs: list[dict] = []
    unreadable: list[str] = []
    for name, path in packs.items():
        if not path.exists():
            unreadable.append(f"{name}: {path}")
        errs.extend(check_pack(name, path))
    # 蓝图另扫一遍报数口径：它不是 manifest、也不是 E1' 的八段发布对象，此前没有任何闸看它。
    for name, rel in BLUEPRINTS.items():
        path = REPO / rel
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append({"pack": name, "code": "blueprint_unreadable",
                         "error": f"{path} 读不到/解析不了：{type(e).__name__}: {e}", "entry": rel})
            continue
        errs.extend(check_report_caliber(name, doc.get("agents") or []))
    return errs, unreadable


def self_test() -> int:
    """六臂：干净基线绿 + 六个坏样本各自红并指名。"""
    base_raw = (REPO / META_PACKS["构造"]).read_text(encoding="utf-8")
    base = json.loads(base_raw)
    if check_pack("构造", Path("(memory)"), copy.deepcopy(base)):
        print("FAIL  基线本身不绿——先修基线再谈负向对照")
        return 1

    arms: list[tuple[str, dict, str]] = []

    no_mem = copy.deepcopy(base)
    no_mem.pop("memory", None)
    arms.append(("删 memory 段必须红", no_mem, "memory"))

    no_res = copy.deepcopy(base)
    no_res["agents"][0].pop("resources", None)
    arms.append(("删某岗 resources 必须红", no_res, "resources.mcpTools"))

    no_board = copy.deepcopy(base)
    no_board.pop("board", None)
    arms.append(("删 board 必须红", no_board, "board"))

    with_publish = copy.deepcopy(base)
    with_publish["mcpServers"].append(
        {"registryName": "facet-market-mcp", "allowTools": ["facet_market_pack_publish"]})
    arms.append(("加 publish 必须红", with_publish, "facet_market_pack_publish"))

    same_minute = copy.deepcopy(base)
    for a in same_minute["agents"]:
        a["cadence"]["cron"] = "23 10 * * 1"
    arms.append(("同分钟节奏必须红", same_minute, "间隔"))

    persona_var = copy.deepcopy(base)
    persona_var["agents"][0]["persona"] = persona_var["agents"][0]["persona"] + "\n上下文：{{fleetContext}}"
    arms.append(("persona 含 {{变量}} 必须红", persona_var, "模板变量"))

    twice = copy.deepcopy(base)
    twice["agents"][0]["cadence"]["promptTemplate"] = (
        "先报数再补细节，取完数立刻调 report_status。" + twice["agents"][0]["cadence"]["promptTemplate"])
    arms.append(("一轮要求报两次必须红", twice, "一轮报两次"))

    hourly = copy.deepcopy(base)
    hourly["agents"][0]["cadence"]["cron"] = "*/5 * * * *"
    arms.append(("高频 cron 超本线上限必须红", hourly, "本线上限"))

    unparsable = copy.deepcopy(base)
    unparsable["agents"][0]["cadence"]["cron"] = "@daily"
    arms.append(("cron 判不了必须红（判不了≠通过）", unparsable, "无法换算"))

    # 旧口径回归臂：把已被证伪的「每回合 3 次」塞回 persona。2026-11-13 实测它真的活在
    # blueprint.json 里而没有任何闸看得到；这条臂保证「再写回去」会红。
    stale_caliber = copy.deepcopy(base)
    stale_caliber["agents"][0]["persona"] = (
        stale_caliber["agents"][0]["persona"] + "\n`report_status` 一回合最多调 3 次（服务端限流）。")
    arms.append(("旧口径「每回合 3 次」必须红", stale_caliber, "每回合的额度"))

    # M8 回归臂：未解析的 pack-id 占位符不许活到发布产物里。2026-11-13 实测它就是
    # 活在 meta-grader 的 fleet.members[0].target.packId 上、并随 v1–v3 上了架。
    stale_ph = copy.deepcopy(base)
    stale_ph["fleet"]["members"][0]["target"] = {
        "kind": "pack-preset", "packId": "{{PACK_ID:meta-grader}}", "presetId": "x"}
    arms.append(("未解析 pack-id 占位符必须红", stale_ph, "未解析的"))

    # M9 回归臂：两条真判据各一条。第一版还写了第三条「有 cron 就必须有入边」，
    # 它把 meta-grader 的审计岗判红了——而市场当场用 `auditor_not_orchestratable`
    # 拒掉了我据此加的边。审计官不进工作图；普通岗的定时任务由 reconcileFleet 按
    # member.cadence 建，也与边无关。所以那条判据换成**反向臂**（见下方 reverse）。
    dangling = copy.deepcopy(base)
    dangling["fleet"]["orchestration"]["edges"].append(
        {"kind": "handoff", "from": "hc-assembler", "to": "hc-assemblre"})
    arms.append(("边的 ref 拼错必须红", dangling, "不是本舰队成员"))

    wrong_cron = copy.deepcopy(base)
    for e in wrong_cron["fleet"]["orchestration"]["edges"]:
        if e.get("kind") == "trigger" and e.get("to") == "hc-architect":
            e["from"] = "cron(53 10 * * 1)"
    arms.append(("trigger 的 cron 与岗位节律不一致必须红", wrong_cron, "两处节律不一致"))

    # M10 回归臂：2026-11-14 首轮实测——评测岗 persona 写「SQL 真值…直接用查询算」，而它的
    # 工具面里没有任何 SQL 工具；模型在工具面里找通道，19 步烧光 480s 预算、零报数收尾。
    sql_claim = copy.deepcopy(base)
    sql_claim["agents"][0]["persona"] = (
        sql_claim["agents"][0]["persona"] + "\n①SQL 真值：回合表里的三个数是确定性的——直接用查询算。")
    arms.append(("声称有 SQL 通道而工具面没有必须红", sql_claim, "声称有 SQL"))

    # M11 回归臂：预算被静默夹紧与两份不一致，各一条。
    over_budget = copy.deepcopy(base)
    over_budget["fleet"]["members"][0]["cadence"]["turnTimeoutMs"] = 45 * 60 * 1000
    arms.append(("预算超平台上界必须红（会被静默夹紧）", over_budget, "静默夹紧"))

    budget_split = copy.deepcopy(base)
    budget_split["fleet"]["members"][0]["cadence"]["turnTimeoutMs"] = 900000
    budget_split["agents"][0]["cadence"]["turnTimeoutMs"] = 300000
    arms.append(("成员预算与服务岗不一致必须红", budget_split, "生效的是成员那份"))

    # M12 回归臂：2026-11-14 实历——persona 写「只对拍 1 支」、promptTemplate 仍写「至多 3 支」，
    # 真跑那轮按 3 支走。这条臂用「两份都给数且不同」构造。
    split_cap = copy.deepcopy(base)
    split_cap["agents"][0]["cadence"]["promptTemplate"] = (
        "本回合对至多 5 个对象做一件事。" + split_cap["agents"][0]["cadence"]["promptTemplate"])
    split_cap["agents"][0]["persona"] = split_cap["agents"][0]["persona"] + "\n每回合至多 2 个对象。"
    arms.append(("两份正文的分批上限不一致必须红", split_cap, "两份不一致"))

    # M13 回归臂：2026-11-14 实测——包级 `fleet` 只允许 auditor_submit_verdict，评测岗因此
    # 拿不到 report_status，整轮跑完一条报数都没落。判据要能在「声明了 fleet 却没留报数口」上红。
    no_report_tool = copy.deepcopy(base)
    no_report_tool["mcpServers"].append({"registryName": "fleet", "allowTools": ["auditor_submit_verdict"]})
    for a in no_report_tool["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["resources"].setdefault("mcpTools", {})["fleet"] = ["auditor_submit_verdict"]
    arms.append(("声明的工具面里没有 report_status 必须红", no_report_tool, "没有 `report_status`"))

    # M14 回归臂：2026-11-14 审计岗实测——有 fleet_turns_read 没有 fleet_list，
    # 于是它去翻工作区找 fleetId（31 步）。
    no_locator = copy.deepcopy(base)
    for a in no_locator["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["resources"]["mcpTools"] = {"fleet-architect": ["fleet_turns_read", "fleet_spec_read"]}
    arms.append(("按舰队取数却没有 fleet_list 必须红", no_locator, "没给 `fleet_list`"))

    # M15 回归臂：2026-11-14 审计岗实测——只有 auditor_submit_verdict（要 itemId），
    # 评测结论不产生待决事项 ⇒ 两次「Decision not found」后空手收尾。
    verdict_only = copy.deepcopy(base)
    for a in verdict_only["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["resources"]["mcpTools"] = {"fleet": ["auditor_submit_verdict"]}
    arms.append(("结论只挂在 auditor_submit_verdict 上必须红", verdict_only, "Decision not found"))

    # M16 回归臂：2026-11-14 实历两形态——① 评测岗正文写 `dissent_reasons` 三槽，
    # 而 kpiSlots 只声明 verdicts/blocks；② 架构岗正文「报 `segments_done` 与
    # `segments_left`」，而 kpiSlots 只声明 drafts_landed/source_gaps。两形态服务端都
    # 静默收进 details，卡片上永远少槽。臂里必须**指名**未声明的那个键（第一版只要求
    # 「命中某个报数槽」的错，结果被一条 `fleet` 的误判凑成了 PASS）。
    phantom_slot = copy.deepcopy(base)
    for a in phantom_slot["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["cadence"]["promptTemplate"] = (
                a["cadence"]["promptTemplate"] + " 报数含 `drafts_landed` / `phantom_slot` 三槽。")
            break
    arms.append(("正文承诺了未声明的报数槽必须红", phantom_slot, "`phantom_slot`"))

    orphan_and = copy.deepcopy(base)
    for a in orphan_and["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["cadence"]["promptTemplate"] = (
                a["cadence"]["promptTemplate"] + " 收尾报 `orphan_done` 与 `orphan_left`。")
            break
    arms.append(("「报 X 与 Y」两个键都未声明必须红", orphan_and, "`orphan_done`"))

    # M16 第三种写法：「报数含 A / B / C」。2026-11-14 由线上产物侧对账
    # （check_live_fleets.py --slots）先发现——提案岗正文写了四槽、编制只声明三槽。
    list_form = copy.deepcopy(base)
    for a in list_form["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["cadence"]["promptTemplate"] = (
                a["cadence"]["promptTemplate"] + " 报数含 `drafts_landed` / `phantom_list` 两槽。")
            break
    arms.append(("「报数含 A / B」里有未声明的键必须红", list_form, "`phantom_list`"))

    # M17 回归臂（2026-11-14 重写后的三向）：旧版判「承诺落盘⇒mcpTools 里要有落盘工具」，
    # 三件候选（fleet_draft=干跑 / blueprint_save=模板落库且属另一台 MCP / propose_action=提审）
    # 无一能落工作区文件 ⇒ 判据结构上不可能绿，唯一能让它绿的动作就是「发提审权」，而实况正相反：
    # 提审权落在两个不提审的岗上、被指定提审的装配岗没有。三向各证一次。
    tool_no_duty = copy.deepcopy(base)
    for a in tool_no_duty["agents"]:
        if a.get("id") == "hc-architect":
            a["resources"]["mcpTools"].setdefault("actions-mcp", []).append("propose_action")
            a["cadence"]["promptTemplate"] = a["cadence"]["promptTemplate"].replace(
                "propose_action", "提案工具")
    arms.append(("提审权挂在没有提审职责的岗上必须红", tool_no_duty, "提审权挂在没这项职责的岗上"))

    duty_no_tool = copy.deepcopy(base)
    for a in duty_no_tool["agents"]:
        if a.get("id") == "hc-assembler":
            for srv in [k for k, v in a["resources"]["mcpTools"].items()
                        if "propose_action" in (v or [])]:
                a["resources"]["mcpTools"][srv] = [
                    t for t in a["resources"]["mcpTools"][srv] if t != "propose_action"]
                if not a["resources"]["mcpTools"][srv]:
                    del a["resources"]["mcpTools"][srv]
    arms.append(("正文说由本岗提审而手里没有提审工具必须红", duty_no_tool, "说了也做不到"))

    two_proposers = copy.deepcopy(tool_no_duty)
    for a in two_proposers["agents"]:
        if a.get("id") == "hc-architect":
            a["persona"] = a["persona"] + "\n收口后由本岗提审，提交铸造提案。"
    arms.append(("两个岗同时持提审权必须红", two_proposers, "重复铸造"))

    # M22 回归臂：2026-11-14 我自己只改了 agents[] 那份，members 仍是旧文，
    # 而核对拿「线上 vs members」比，两边都旧 ⇒ 通过而缺陷仍在。
    diverge = copy.deepcopy(base)
    for a in diverge["agents"]:
        if a.get("cadence", {}).get("promptTemplate"):
            a["cadence"]["promptTemplate"] = a["cadence"]["promptTemplate"] + " 收口后由本岗提审。"
            break
    arms.append(("两份 promptTemplate 不一致必须红", diverge, "不一致"))

    # 同族反向臂不并进 arms（那一轮要求「必须命中 needle」）：见下方「反向臂十」。

    # M21 回归臂：同一个过时事实曾同时活在五处（persona / 两份 prompt / 两份技能正文 / 四份 i18n）。
    stale_seg = copy.deepcopy(base)
    stale_seg["skills"][0]["content"] = stale_seg["skills"][0]["content"].replace("九段", "六段", 1)
    arms.append(("技能正文把草稿写成六段必须红", stale_seg, "六段"))

    stale_head = copy.deepcopy(base)
    stale_head["i18n"]["ja"]["notes"] = stale_head["i18n"]["ja"]["notes"] + " 二崗で一つの承認。"
    arms.append(("i18n 里的岗数与编制不符必须红", stale_head, "编制变了而说明书没跟着变"))

    # M18 回归臂：2026-11-14 优化团队两岗连撞 `budget soft stop (480s of 600s)`——
    # 正文逐队调 fleet_turns_read，而 cadence 没写 turnTimeoutMs（缺省 600s ⇒ 软停 480s），
    # 而同类回合实测 438-531s。两条臂：完全没写、写了但太小。
    no_budget = copy.deepcopy(base)
    for a in no_budget["agents"]:
        p = (a.get("cadence") or {}).get("promptTemplate")
        if p is not None:
            a["cadence"]["promptTemplate"] = p + " 对至多 5 个团队调 `fleet_turns_read`。"
    for m in no_budget["fleet"]["members"]:
        m.get("cadence", {}).pop("turnTimeoutMs", None)
    arms.append(("逐队读回合史却没声明预算必须红", no_budget, "却没有声明 `turnTimeoutMs`"))

    low_budget = copy.deepcopy(no_budget)
    for m in low_budget["fleet"]["members"]:
        m["cadence"]["turnTimeoutMs"] = 600_000
    arms.append(("逐队读回合史而预算仍停在缺省值必须红", low_budget, "低于实测下限"))

    # M19 回归臂：2026-11-14 实历——architect 声明四槽，四份语种覆盖层只写前两槽，
    # `validate_manifest.py`（真校验器）4 错，而本闸当时全绿。
    partial_i18n = copy.deepcopy(base)
    for lang_block in (partial_i18n["fleet"].get("i18n") or {}).values():
        for ref, entry in ((lang_block or {}).get("members") or {}).items():
            slots = (entry or {}).get("kpiSlots")
            if isinstance(slots, dict) and len(slots) > 1:
                entry["kpiSlots"] = dict(list(slots.items())[:1])
                break
        break
    arms.append(("语种覆盖层少译一个槽必须红", partial_i18n, "partial overlay"))

    # M20 回归臂：2026-11-14 实历——装配岗把草稿工具面写成 `fd-memory.memory_write_page`
    # （名字查得对、形状是错的），而 M2 只判 `mcpTools` 非空，值长什么样没人看。
    dot_form = copy.deepcopy(base)
    for a in dot_form.get("agents") or []:
        mt = (a.get("resources") or {}).get("mcpTools")
        if isinstance(mt, dict) and mt:
            srv = next(iter(mt))
            mt[srv] = [f"{srv}.some_tool"]
            break
    arms.append(("逐岗工具面写成点号必须红", dot_form, "点号写法"))

    undeclared_srv = copy.deepcopy(base)
    for a in undeclared_srv.get("agents") or []:
        mt = (a.get("resources") or {}).get("mcpTools")
        if isinstance(mt, dict):
            mt["fd-memory"] = ["memory_write_page"]
            break
    arms.append(("逐岗工具面引用包级未声明的服务必须红", undeclared_srv, "没声明它"))

    # M23 正向臂一：写侧删掉重建条款必须红。2026-11-15 实况是反过来发现的——线上那轮
    # 就是旧版 prompt（无重建句）跑出来的，报数声称已交接、盘上没有；把那句删回去，
    # 闸必须红出「没有一句说它不在了怎么办」。
    no_rebuild = copy.deepcopy(base)
    rebuild_sent = ("**两份小账（`draft-status.md` / `skills-pending.md`）不存在就先按草稿重建**"
                    "（≤1 KiB / ≤4 KiB）——它们是下游岗的唯一读取入口，丢了等于把 107 KiB 整稿"
                    "重新塞回去（2026-11-15 实况：报数里声称已交接、文件其实不存在）；")
    stripped = 0
    for a in no_rebuild["agents"]:
        pt = (a.get("cadence") or {}).get("promptTemplate") or ""
        if rebuild_sent in pt:
            a["cadence"]["promptTemplate"] = pt.replace(rebuild_sent, "")
            stripped += 1
    for m in no_rebuild.get("fleet", {}).get("members") or []:
        pt = (m.get("cadence") or {}).get("promptTemplate") or ""
        if rebuild_sent in pt:
            m["cadence"]["promptTemplate"] = pt.replace(rebuild_sent, "")
    arms.append(("小交接账删掉重建条款必须红", no_rebuild,
                 "没有任何一方写了「它不在了怎么办」"))

    # M23 正向臂二：读侧指一份没人写的文件必须红（改名即造出这种形状）。
    # 顺带证否定句不算消费：技能作者那句里的「不要读整份 blueprint-draft.md」不得把它
    # 误判成被消费的账——它是设计内的禁止项。
    ghost = copy.deepcopy(base)
    for m in ghost.get("fleet", {}).get("members") or []:
        if m.get("ref") == "hc-skill-author":
            pt = (m.get("cadence") or {}).get("promptTemplate") or ""
            m["cadence"]["promptTemplate"] = pt.replace(
                "workspace/skills-pending.md", "workspace/skills-pending-v2.md")
    arms.append(("读侧指向没人写的交接单必须红", ghost, "没有任何岗被要求写它"))



    passed = 0
    with tempfile.TemporaryDirectory(prefix="fd-meta-selftest-") as td:
        for i, (label, obj, needle) in enumerate(arms):
            p = Path(td) / f"arm{i}.json"
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            got = check_pack("构造", p)
            hit = any(needle in e["error"] or needle in e["entry"] for e in got)
            ok = bool(got) and hit
            detail = next((e["error"] for e in got
                           if needle in e["error"] or needle in e["entry"]), "未指名")
            print(f"  {'PASS' if ok else 'FAIL'}  {label} — {detail[:110]}")
            passed += 1 if ok else 0

    # 反向臂（不能并进上面那轮：那轮要求「命中 needle」，这条要求「一条都不许有」）：
    # 引用旧口径来**反驳**它不得误判——三份 manifest 里现在就是这么写的（「旧口径写
    # 『一回合最多 3 次』会让人一轮就把 12 小时额度用尽」）。判据只看字面会把正确文案判红。
    cites = copy.deepcopy(base)
    cites["agents"][0]["persona"] = (
        cites["agents"][0]["persona"]
        + "\n`report_status` 的限流是 3 条 / 720 分钟，不是每回合 3 次——"
          "旧口径写「一回合最多 3 次」会让人一轮就把 12 小时额度用尽。")
    reverse_ok = not check_report_caliber("构造", cites["agents"])
    print(f"  {'PASS' if reverse_ok else 'FAIL'}  引用旧口径来反驳不得误判 — "
          f"{'未误判' if reverse_ok else '把反驳文案判成了旧口径'}")
    passed += 1 if reverse_ok else 0

    # 反向臂二：审计官/普通岗「有 cron、无入边」都不得判红。
    # 依据是源码而不是直觉：`fleet-reconcile.js` 的 reconcileFleet 对每个有 cadence
    # 的成员建定时任务（与编导边无关），而 `fleet-orchestration.js` 显式拒绝审计官
    # 当边端点（auditor_not_orchestratable）。这条臂防的是「把合规编制判红」——
    # 第一版判据正是这样把 meta-grader 判红、并按错的结论去改包的。
    lone = copy.deepcopy(base)
    lone["fleet"]["members"].append(
        {"ref": "hc-auditor", "title": "审计岗", "kpiSlots": [{"key": "verdicts"}],
         "target": {"kind": "auditor", "id": "hc-auditor"},
         "cadence": {"cron": "47 12 * * 1"}})
    no_edge = [e for e in lone["fleet"]["orchestration"]["edges"] if e.get("to") != "hc-assembler"]
    lone["fleet"]["orchestration"]["edges"] = no_edge  # 装配岗与审计岗都无入边
    # 反向臂三：显式声明「没有 SQL 通道」不得误判（否定只在同句生效，所以这句话必须
    # 和「SQL」写在同一句里）。
    sql_denied = copy.deepcopy(base)
    sql_denied["agents"][0]["persona"] = (
        sql_denied["agents"][0]["persona"] + "\n①回合表真值：你没有 SQL 通道，别去找；用 fleet_turns_read 读。")
    denied_ok = not check_capability_executable("构造", sql_denied)
    print(f"  {'PASS' if denied_ok else 'FAIL'}  显式声明无 SQL 通道不得误判 — "
          f"{'未误判' if denied_ok else '把否定声明判成了能力承诺'}")
    passed += 1 if denied_ok else 0

    lone_errs = check_member_triggers("构造", lone)
    lone_ok = not lone_errs
    print(f"  {'PASS' if lone_ok else 'FAIL'}  有 cron 无入边不得误判（平台按 cadence 建任务）— "
          f"{'未误判' if lone_ok else lone_errs[0]['error'][:90]}")
    passed += 1 if lone_ok else 0

    # 反向臂四（M16）：三态取值 `agree/dissent/block` 与槽位名写在同一括号里不得误判。
    # 判据按 `；` 切段后只看「槽」前面那一段，正是为了不吃到前一段的裁决枚举——
    # 这是第一版设想的写法，不切段就会把合规文案判红。
    enum_next = copy.deepcopy(base)
    declared_keys = [s.get("key") for m in (enum_next.get("fleet") or {}).get("members") or []
                     for s in (m.get("kpiSlots") or []) if isinstance(s, dict) and s.get("key")]
    if declared_keys:
        for a in enum_next["agents"]:
            if a.get("cadence", {}).get("cron"):
                a["cadence"]["promptTemplate"] = (
                    a["cadence"]["promptTemplate"]
                    + "（agree/dissent/block；" + "/".join(declared_keys[:3]) + " 三槽，理由进 summary）")
                break
        enum_ok = not check_declared_slots("构造", enum_next)
    else:
        enum_ok = True
    print(f"  {'PASS' if enum_ok else 'FAIL'}  三态枚举与槽位同括号不得误判 — "
          f"{'未误判' if enum_ok else '把 agree/dissent/block 当成了槽位名'}")
    passed += 1 if enum_ok else 0

    # 反向臂五（M16）：括注里说「某个键不是槽位」不得被读成承诺；驼峰词
    # `kpiSlots` 也不得被切成 `kpi` + `lots` 两个假槽（两个缺陷 2026-11-14 同时出现过）。
    negation = copy.deepcopy(base)
    declared2 = [s.get("key") for m in (negation.get("fleet") or {}).get("members") or []
                 for s in (m.get("kpiSlots") or []) if isinstance(s, dict) and s.get("key")]
    if len(declared2) >= 2:
        for a in negation["agents"]:
            if a.get("cadence", {}).get("cron"):
                a["cadence"]["promptTemplate"] = (
                    a["cadence"]["promptTemplate"]
                    + f" 报数含 `{declared2[0]}` / `{declared2[1]}` 两槽"
                      "（`gold_source` 不是槽位、写进 summary 正文——槽位只认 kpiSlots 声明过的键）。")
                break
        negation_ok = not check_declared_slots("构造", negation)
    else:
        negation_ok = True
    print(f"  {'PASS' if negation_ok else 'FAIL'}  括注否定与驼峰词不得误判 — "
          f"{'未误判' if negation_ok else '把否定句读成了槽位承诺（或把 kpiSlots 切成 kpi/lots）'}")
    passed += 1 if negation_ok else 0

    # 反向臂六（M17）：只**读**草稿的岗不得判红。第一版把裸名词「草稿」也算写承诺，
    # 于是盘账岗/装配岗（「拿到草稿」「读草稿」）全被判红——那是在读。现只认写动词短语。
    # 只喂那一个岗：base 里其它 cron 岗的正文自身带写承诺（技能作者就是），整份判会把
    # 别人的红算到这条反向臂头上——反向臂要证明的是「判定逻辑不吃读草稿」，不是「整包绿」。
    read_only = copy.deepcopy(base)
    only = None
    for a in read_only["agents"]:
        if a.get("cadence", {}).get("cron"):
            a["resources"]["mcpTools"] = {"fleet-architect": ["fleet_spec_read"]}
            a["persona"] = "你是清点岗。一、先读草稿：拿到草稿后只做清点，不写任何东西。"
            only = a
            break
    read_ok = not check_write_channel("构造", {"agents": [only]})
    print(f"  {'PASS' if read_ok else 'FAIL'}  只读草稿不得误判 — "
          f"{'未误判' if read_ok else '把「读草稿」当成了写承诺'}")
    passed += 1 if read_ok else 0

    # 反向臂十（M22）：只写 `fleet.members[]` 一份（平台就按它跑）不得判红——
    # 否则「另一处可省略」这个合法写法会被判成缺陷，而全仓多数包正是只写一处。
    only_member = copy.deepcopy(base)
    for a in only_member["agents"]:
        a.get("cadence", {}).pop("promptTemplate", None)
    ok10 = not check_cadence_copies("构造", only_member)
    print(f"  {'PASS' if ok10 else 'FAIL'}  只在 members 写 promptTemplate 不得判红 — "
          f"{'未误判' if ok10 else '把「只写一处」判成了两份不一致'}")
    passed += 1 if ok10 else 0

    # 反向臂八（M21）：`Four-post handoff contract` 讲的是**交接链上的四岗**，不是舰队编制
    # （marketing 那份舰队五岗、交接链四岗，两句话都对）；按裸 -post 判会假红。
    handoff_ok = copy.deepcopy(base)
    handoff_ok["skills"][0]["content"] = (
        handoff_ok["skills"][0]["content"]
        + "\n\n四岗 handoff 契约：选题→成稿→放行→落稿；Four-post handoff contract.")
    ok8 = not check_stated_counts("构造", handoff_ok)
    print(f"  {'PASS' if ok8 else 'FAIL'}  交接链的岗数不是编制陈述不得判红 — "
          f"{'未误判' if ok8 else '把「四岗交接链」当成舰队只有四岗'}")
    passed += 1 if ok8 else 0

    # 反向臂九（M21）：没有 `fleet.members` 的包（角色包/技能包）不声明编制 ⇒ 判不出，不是红。
    roles_only = copy.deepcopy(base)
    roles_only["fleet"].pop("members", None)
    ok9 = not check_stated_counts("构造", roles_only)
    print(f"  {'PASS' if ok9 else 'FAIL'}  不声明编制的包不得判红 — "
          f"{'未误判' if ok9 else '把「没有 members」当成「岗数是 0」'}")
    passed += 1 if ok9 else 0

    # 反向臂七（M20）：合规的裸名工具面不得判红——否则 M20 会把现在跑着的包全判红，
    # 而那正说明判据写错了（8 个带逐岗面的包、370 处 allowTools 全是这种形状）。
    bare_ok = not check_toolface_shape("构造", copy.deepcopy(base))
    print(f"  {'PASS' if bare_ok else 'FAIL'}  裸名工具面不得误判 — "
          f"{'未误判' if bare_ok else '把合规的两层 map 判红了'}")
    passed += 1 if bare_ok else 0

    # 反向臂十一（M23）：否定句里的 workspace 文件不是消费——「不要读整份
    # blueprint-draft.md」是设计内的禁止项；把它算成被读的账会让整包常年红。
    # 基线绿已证了一遍（全包跑 check_pack），这里对 M23 单独再证一次，免得
    # 别的判据恰好也绿时看不出这条反向臂的功劳。
    neg_ok = not check_handoff_ledgers("构造", copy.deepcopy(base))
    print(f"  {'PASS' if neg_ok else 'FAIL'}  否定句里的 workspace 文件不得算消费 — "
          f"{'未误判' if neg_ok else '把「不要读整份 X」当成了消费 X'}")
    passed += 1 if neg_ok else 0

    total = len(arms) + 11

    print(f"self-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="元线三包结构门（三支柱 / 写面禁令 / 节奏错峰）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    errs, unreadable = run()
    if unreadable:
        for u in unreadable:
            print(f"✗ 制品读不到：{u}", file=sys.stderr)
        return 2

    for name in META_PACKS:
        n = len([e for e in errs if e["pack"] == name])
        print(f"  {'✅' if n == 0 else '❌'} {name:<4} {META_PACKS[name]}  {n} 处问题")
    for e in errs:
        print(f"    [{e['pack']}][{e['code']}] {e['entry']}: {e['error']}")
    print(f"meta-line {'绿' if not errs else f'红（{len(errs)} 处）'}")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
