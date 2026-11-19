#!/usr/bin/env python3
"""漂移哨：源 manifest.json 必须逐字段等于市场现行版，且对外字段无内部物。

背景（2026-10-30 建制）：市场是唯一分发通道，源仓只是铸造输入。历史上两处失控——
  ① 词表改名只改了市场没回填源 → 下次重发把改名冲回旧词；
  ② 岗位/结构改铸只改了市场没回填 template → 重铸把结构打回老版。
本哨把「源=市场真值」立成可机检的硬门。发布任何新版前后都跑一次。

通道：读市场用 facet 代理口（同 publish_pack.py 的 x-market-token/x-facet-user）。
  base 默认 https://facet.finddatatech.cloud；内网可 --base http://127.0.0.1:3200（需 port-forward 或 pod 内）。

用法：
    python3 scripts/check_drift.py                      # 对拍全部台账包
    python3 scripts/check_drift.py --base http://127.0.0.1:3200
    python3 scripts/check_drift.py --pack fleet-brief-report   # 只查一枚（slug）
退出码：0=全绿；1=有漂移或有内部物泄漏；2=通道不可用（不误报为漂移）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "README.md"


def leaf_diff(a, b, prefix: str = "") -> dict[str, list[str]]:
    """递归到叶子，判定差异方向：仅市场有 / 仅仓库有 / 两侧都不同。

    为什么非要到叶子：`i18n` 这个键两侧都存在，但仓库只有 en、市场有 en/es/fr/ja
    ——顶层的「两侧都不同」把「仓库落后三个语种」和「两边各改了同一处」混成一句，
    而这两件事的处置相反（前者从市场回填，后者必须人工比对）。2026-11-13 就是这么混的。
    """
    out: dict[str, list[str]] = {"仅市场有": [], "仅仓库有": [], "两侧都不同": []}
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            sub = leaf_diff(a.get(k) if k in a else ...,
                            b.get(k) if k in b else ...,
                            f"{prefix}.{k}" if prefix else k)
            for bucket, items in sub.items():
                out[bucket] += items
        return out
    in_a, in_b = a is not ..., b is not ...
    if in_b and not in_a:
        out["仅市场有"].append(prefix)
    elif in_a and not in_b:
        out["仅仓库有"].append(prefix)
    elif a != b:
        out["两侧都不同"].append(prefix)
    return out


def _dig(doc, path: str):
    """按 `a.b.c` 取叶子值；取不到返回 "(缺)"——与「值为 None」区分开。"""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return "(缺)"
    return cur


def workspace_roots() -> list[Path]:
    """finddata 工作区根的候选链（本仓可能在工作区内，也可能是工作区之外的 worktree）。

    为什么不能只写 `REPO.parent`：本仓的 ERP 模板包不在本仓里，而在兄弟仓
    `fd-erp-vertical-templates/` 下，台账因此也带工作区根相对路径。当本仓从
    `<ws>/fleet-packs` 变成 `<ws>` 之外的 worktree（例如 /Users/x/fp-input-…）时，
    `REPO.parent` 就不再是工作区根，六个模板包会被报成「源缺失」——而它们其实在
    市场的 v3/v9 上跑着。判据看不见的包，等于没有判据。
    显式优先（`FP_WORKSPACE_ROOT`），其余按推断顺序，命中即用。
    """
    cands: list[Path] = []
    env = os.environ.get("FP_WORKSPACE_ROOT", "").strip()
    if env:
        cands.append(Path(env).expanduser())
    cands += [REPO.parent, REPO.parent / "finddata", REPO.parent.parent / "finddata"]
    out: list[Path] = []
    for p in cands:
        if p not in out:
            out.append(p)
    return out

ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`\s*\|\s*v?(\d+)?\s*\|\s*([^|]+?)\s*\|")
DEFAULT_BASE = "https://facet.finddatatech.cloud"
DEFAULT_AUTHOR = "operator@example.com"
# 对外可见字段禁词（内部拓扑/阶段词/仓路径）。命中即泄漏，非漂移。
LEAK_RE = re.compile(
    r"fleet-packs/|/blueprints/schema\.md|\bL[0-9]\b|\bharness\b|add-[a-z0-9][a-z0-9-]+|"
    r"fd-craw|cheap-\d|sub2api|127\.0\.0\.1|finddatatech\.cloud",
    re.IGNORECASE,
)
# 只扫这些对外字段的字符串值（agents persona 里的工具面引用如 fd-info 属正当，故不扫 agents/skills 正文）
OUTWARD_FIELDS = ("name", "description", "tags", "notes", "i18n")
# 已知挂账泄漏：命中降级为 🔶 不判红。2026-10-30 tags 词面清扫后清空（harness 已出 tags；
# 技能 id harness-constructor 在 skills 数组内，不属对外扫描面 OUTWARD_FIELDS）。
KNOWN_LEAKS = {}


def load_ledger() -> list[dict]:
    rows = []
    for lineno, line in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), start=1):
        # 5.4 tolerance: a line that LOOKS like a ledger row (table row whose
        # second cell is a packId-shaped token or names a pack) but fails
        # ROW_RE must be reported with its line number, never silently
        # skipped — a silently-dropped row is a pack invisible to drift
        # checking (the edu-watch 2026-11-07 incident class).
        if ROW_RE.match(line):
            m = ROW_RE.match(line)
            rows.append({"slug": m.group(1), "packId": m.group(2),
                         "version": int(m.group(3)) if m.group(3) else None,
                         "src": m.group(4).strip()})
        elif _looks_like_ledger_row(line):
            print(f"⚠️ 台账行格式异常（README.md:{lineno}）不解析: {line.strip()[:80]}",
                  file=sys.stderr)
            MALFORMED_ROWS.append(lineno)
    return rows


# Lines that look like ledger rows but failed ROW_RE: start with a table pipe,
# are inside the ledger section, and mention a pack-ish first cell. Tolerated
# but loudly reported (caller decides exit semantics via MALFORMED_ROWS).
MALFORMED_ROWS: list[int] = []


def _looks_like_ledger_row(line: str) -> bool:
    """True = the line claims to be a machine-ledger data row but failed ROW_RE.

    Deliberate non-rows must NOT match: the ledger header row (``| pack |``),
    separator rows, and the documented "packId 未铸造" placeholder rows.
    A data row's second cell is a backticked packId or the literal marker;
    anything slug-shaped with a packId cell that LOOKS like it should have
    backticks (has a version cell and a src path cell) is a malformed row.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = [c.strip() for c in stripped.split("|")[1:-1]] if stripped.endswith("|") else [c.strip() for c in stripped.split("|")]
    if len(cells) < 4:
        return False
    slug = cells[0]
    if not re.match(r"^[a-z0-9][a-z0-9-]+$", slug):
        return False  # header ("pack"), separators, prose — not data rows
    if "未铸造" in line or "packId 未" in line:
        return False  # documented placeholder (erp-ledger-kernel-pack)
    pack_cell = cells[1]
    if pack_cell.startswith("`") and pack_cell.endswith("`"):
        return False  # proper backticked packId would have matched ROW_RE
    # malformed: slug-shaped + version cell + src-ish cell, but packId cell
    # isn't a backticked token — the row means to be machine-read.
    return bool(re.match(r"^v?\d+$", cells[2].strip()) if len(cells) > 2 and cells[2].strip() else False)


def token() -> str:
    t = os.environ.get("FACET_INTERNAL_TOKEN", "").strip()
    if t:
        return t
    try:
        out = subprocess.run(["kubectl", "-n", "fd-prod", "get", "secret", "facet-secrets",
                              "-o", "jsonpath={.data.FACET_INTERNAL_TOKEN}"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return base64.b64decode(out.stdout.strip()).decode()
    except Exception:
        pass
    return ""


def fetch(base: str, pack_id: str, tok: str) -> dict | None:
    identity = base64.urlsafe_b64encode(json.dumps({"email": DEFAULT_AUTHOR, "groups": ["creators", "platform"]}).encode()).decode().rstrip("=")
    req = urllib.request.Request(f"{base.rstrip('/')}/api/packs/{pack_id}",
                                 headers={"x-market-token": tok, "x-facet-user": identity})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code} 读 {pack_id}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  ! 通道异常 读 {pack_id}: {e}", file=sys.stderr)
        return None
    p = j.get("pack") or j
    return {"version": p.get("version"), "manifest": p.get("manifest") or p}


# ── MCP 引用解析（2026-11-08）────────────────────────────────────────────────
#
# 包里的 `mcpServers[].registryName` 有三个合法来源，且**同一台服务器在不同形态下名字不同**：
#   市场目录（facet /api/mcp-catalog）—— 唯一带 tool_list 的来源；
#   部署基线（paas/mcp.json）—— 运维启用哪些行，行名即引用名；
#   打包形态（paas/platform.bundle.json）—— 镜像/桌面装出来的行名（如 actions-mcp）。
# 装包时 paas 依次按"市场条目 → 同名本地行 → awaiting-local-binding"解析。所以：
#   · 引用名必须在三者之一里存在，否则就是真笔误（会被静默落成 awaiting-local-binding）；
#   · 工具白名单只在**声明了工具**的来源上比（只有市场目录带 tool_list）。
# 2026-11-08 教训：只查前两者就把 actions-mcp / blueprint-author 误判成悬空（它们都在 bundle 里）。
PAAS = REPO.parent / "paas"
MARKET_SNAPSHOT = PAAS / "market-catalog.json"
BASELINE = PAAS / "mcp.json"
BUNDLE = PAAS / "platform.bundle.json"


def _live_market_servers(tok: str) -> list[dict] | None:
    """线上市场目录（26 条，带 tool_list）。拿不到就返回 None，由调用方退回快照。"""
    if not tok:
        return None
    req = urllib.request.Request(f"{DEFAULT_BASE.rstrip('/')}/api/mcp-catalog",
                                 headers={"x-market-token": tok})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  线上市场目录读不到（{e}），退回仓库快照——工具面可能过时", file=sys.stderr)
        return None
    rows = body.get("mcpServers") or body.get("servers") or []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("name"), str)]


def _server_tools(tok: str = "") -> tuple[dict[str, dict], bool]:
    """名字 → {sources: set, tools: set|None}，外加"市场目录是否来自线上"。

    第二项是判据强度的前提：快照会过时（离线那份缺 fd-open-data-mcp），拿残缺清单判
    "名字不存在"就是伪造证据（2026-11-08 一次误报 48 条）。所以只有线上目录时，
    "三种形态都没有"才配判红；退回快照时只告警。
    """
    out: dict[str, dict] = {}

    def add(name: str, source: str, tools=None) -> None:
        if not name:
            return
        ent = out.setdefault(name, {"sources": set(), "tools": tools})
        ent["sources"].add(source)
        if tools is not None:
            ent["tools"] = (ent["tools"] or set()) | tools

    def rows(path: Path, key_getter):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        for row in key_getter(data) or []:
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                yield row

    # 市场目录：线上优先，快照兜底
    market_rows = _live_market_servers(tok)
    market_live = market_rows is not None
    if market_rows is None:
        market_rows = list(rows(MARKET_SNAPSHOT, lambda d: d.get("mcpServers")))
    for row in market_rows:
        declared = row.get("tool_list")
        if declared is None:
            declared = row.get("tools")
        # 该条目没有工具声明 ⇒ 未知（None），不是"零个工具"：否则白名单会被全判多余
        tools = None
        if isinstance(declared, list):
            tools = {t.get("name") for t in declared if isinstance(t, dict) and t.get("name")}
        add(row["name"], "market", tools)
    # 部署基线
    try:
        for name in (json.loads(BASELINE.read_text(encoding="utf-8")) or {}).get("mcpServers", {}):
            add(name, "baseline")
    except Exception:
        pass
    # 打包形态
    try:
        for name in (json.loads(BUNDLE.read_text(encoding="utf-8")) or {}).get("mcpServers", {}):
            add(name, "bundle")
    except Exception:
        pass
    return out, market_live


def check_mcp_refs(manifests: list[tuple[str, dict]], tok: str = "") -> int:
    """每个包的 mcpServers 引用是否可解析、白名单是否落在该服务器的工具面里。"""
    servers, market_live = _server_tools(tok)
    if not servers:
        print("⚠️  MCP 引用未校验（找不到市场快照/部署基线/打包清单）", file=sys.stderr)
        return 0
    if not market_live:
        print("⚠️  MCP 引用按仓库快照校验：快照可能过时，'名字不存在'只告警不判红", file=sys.stderr)
    problems = 0
    for slug, mf in manifests:
        for ref in mf.get("mcpServers") or []:
            name = ref.get("registryName")
            if not isinstance(name, str) or not name:
                continue
            ent = servers.get(name)
            if ent is None:
                if market_live:
                    print(f"❌ {slug:28s} 引用 {name!r} 在三种形态里都不存在（市场目录/部署基线/打包清单）——"
                          f"装包会静默落在 awaiting-local-binding")
                    problems += 1
                else:
                    print(f"⚠️  {slug:28s} 引用 {name!r} 在快照里找不到（快照可能过时，未判红）")
                continue
            tools = ent.get("tools")
            if tools is None:
                continue  # 该来源不声明工具面（基线/bundle 只有 command/args）
            extra = [t for t in (ref.get("allowTools") or []) if t not in tools]
            if extra:
                print(f"❌ {slug:28s} 引用 {name!r} 白名单含该服务器没有的工具: {extra}")
                problems += 1
    return problems


def outward_strings(node, path=""):
    """收集对外字段的字符串值（不深入 agents/skills 正文）。"""
    out = []
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            out += outward_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += outward_strings(v, f"{path}[{i}]")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("FACET_BASE", DEFAULT_BASE))
    ap.add_argument("--pack", help="只查一枚（机器台账 slug）")
    ap.add_argument("--detail", action="store_true",
                    help="漂移时逐叶子打印仓侧/市场侧的值（默认只报方向）")
    args = ap.parse_args()

    tok = token()
    rows = load_ledger()
    if args.pack:
        rows = [r for r in rows if r["slug"] == args.pack]
    if not rows:
        print("台账为空或 slug 不匹配", file=sys.stderr)
        return 2

    drift = leak = unreachable = 0
    by_class: dict[str, list[str]] = {}
    checked_manifests: list[tuple[str, dict]] = []
    for r in rows:
        live = fetch(args.base, r["packId"], tok)
        if live is None:
            unreachable += 1
            print(f"⚠️  {r['slug']:28s} 读不到（通道/权限）")
            continue
        mf = r["src"]
        # Source resolution has two axes, and the sentinel used to hardcode both:
        #   root  — fleet packs live under THIS repo; ERP template packs live in a
        #           sibling repo (fd-erp-vertical-templates/…), so the ledger also
        #           carries finddata-root paths.
        #   name  — fleet dirs carry manifest.json; template packs carry pack.json.
        # Hardcoding `REPO / mf / "manifest.json"` reported 源缺失 for four packs
        # that are live on the market, i.e. it never compared them at all. A gate
        # that cannot see a pack is no gate for that pack.
        src_path = next(
            (p for base in (REPO, *workspace_roots())
             for p in (base / mf / "manifest.json", base / mf / "pack.json")
             if p.exists()),
            None,
        )
        if src_path is None:
            # 把查过的根逐个点名：否则「根没找对」与「源真的没有」在输出上长得一模一样。
            searched = ", ".join(str(b) for b in (REPO, *workspace_roots()))
            print(f"❌ {r['slug']:28s} 源缺失 {mf}/{{manifest,pack}}.json（已查：{searched}）")
            drift += 1
            continue
        src = json.loads(src_path.read_text(encoding="utf-8"))
        checked_manifests.append((r["slug"], src))
        livem = live["manifest"]
        # 逐字段对拍（忽略源里可能多带的 _casting/生成期字段）
        diff = [k for k in set(src) | set(livem)
                if k not in ("_casting",) and src.get(k) != livem.get(k)]
        if diff:
            # 方向比「有差异」重要一个量级：仓落后时发布＝拿旧版盖线上，仓领先时不出片＝白改。
            # 单看 keys=[…] 分不出这两件事，于是每次都要有人手工 diff 一遍。
            # 而「两侧都不同」还要再分一层：正文（中文源）一样、只有显示副轨不一样，
            # 那不是内容分叉而是译文落后——处置完全不同（回填 i18n vs 人工裁决）。
            base_src = {k: v for k, v in src.items() if k not in ("_casting", "i18n")}
            base_live = {k: v for k, v in livem.items() if k not in ("_casting", "i18n")}
            d_body = leaf_diff(base_src, base_live)
            d_i18n = leaf_diff(src.get("i18n") or {}, livem.get("i18n") or {})
            dirs = leaf_diff({k: v for k, v in src.items() if k != "_casting"},
                             {k: v for k, v in livem.items() if k != "_casting"})
            only_market, only_repo, both = dirs["仅市场有"], dirs["仅仓库有"], dirs["两侧都不同"]
            parts = []
            for label, items in (("仅市场有", only_market), ("仅仓库有", only_repo), ("两侧都不同", both)):
                if items:
                    head = ", ".join(items[:6]) + ("…" if len(items) > 6 else "")
                    parts.append(f"{label} {len(items)} 处[{head}]")
            body_empty = not (d_body["仅市场有"] or d_body["仅仓库有"] or d_body["两侧都不同"])
            i18n_count = len(d_i18n["仅市场有"]) + len(d_i18n["仅仓库有"]) + len(d_i18n["两侧都不同"])
            cls = None
            if body_empty and i18n_count:
                cls = "仅显示副轨差异"
                verdict = "→ 仅显示副轨差异（正文中文源逐字一致）：可从市场回填 i18n"
            elif not only_repo and not both:
                cls = "仓库严格落后"
                verdict = "→ 仓库严格落后：别发布，先从市场回填"
            elif not only_market and not both:
                cls = "仓库严格领先"
                verdict = "→ 仓库严格领先：未发布的改动，出片前先核这份是不是要的"
            elif not only_market and not only_repo:
                cls = "同键异文"
                verdict = "→ 同键异文：两侧各改过，必须人工比对，禁任一侧直接覆盖"
            else:
                cls = "双向分叉"
                verdict = "→ 双向分叉：既有仓落后也有仓领先，逐字段人工比对"
            by_class.setdefault(cls, []).append(r["slug"])
            print(f"❌ {r['slug']:28s} 漂移（市场 v{live['version']}）{'；'.join(parts)} {verdict}")
            if args.detail:
                # 报方向之后还差最后一步：两侧各写了什么。没有这一步，人还是得另写脚本
                # 把两份 manifest 抽出来对比——今天两轮都是这么绕过去的。
                for path in only_market + only_repo + both:
                    a = _dig(src, path)
                    b = _dig(livem, path)
                    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
                        sa = json.dumps(a, ensure_ascii=False, sort_keys=True)
                        sb = json.dumps(b, ensure_ascii=False, sort_keys=True)
                    else:
                        sa, sb = str(a), str(b)
                    print(f"      · {path}")
                    print(f"          仓: {sa[:240]}")
                    print(f"          市: {sb[:240]}")
            drift += 1
        elif r["version"] and live["version"] != r["version"]:
            print(f"🔶 {r['slug']:28s} 内容一致但台账版本落后：记 v{r['version']} 市场 v{live['version']}")
        else:
            print(f"✅ {r['slug']:28s} 源=市场 v{live['version']} 逐字段一致")
        # 对外字段泄漏扫描（扫市场现行版，因为那才是用户能看到的）
        known = KNOWN_LEAKS.get(r["packId"], set())
        for path, s in outward_strings({k: livem.get(k) for k in OUTWARD_FIELDS}):
            for hit in LEAK_RE.finditer(s):
                word = hit.group(0).strip("./").split("/")[-1]
                if word in known or hit.group(0) in known:
                    print(f"   🔶 挂账词 {r['slug']} {path}: {hit.group(0)}（词汇清扫批处理）")
                    continue
                print(f"   ⚠️ 泄漏 {r['slug']} {path}: …{s[max(0,hit.start()-30):hit.end()+30]}…")
                leak += 1
    refs = check_mcp_refs(checked_manifests, tok)
    if refs:
        print(f"\n❌ MCP 引用问题 {refs} 处")
    # 汇总表：把逐包结论按「处置」分组。逐行输出能看见每个包，但看不见「今天该做哪几件事」；
    # 而维护动作是按类批处理的（一批回填、一批出片、一批人工裁决）。这张表让
    # 人/舰队不必再手工统计一遍——2026-11-13 我为了这个统计临时写了三段脚本。
    if by_class:
        print("\n──── 按处置分组 ────")
        for cls in ("仅显示副轨差异", "仓库严格落后", "仓库严格领先", "同键异文", "双向分叉"):
            slugs = by_class.get(cls) or []
            if slugs:
                print(f"  {cls}（{len(slugs)}）：{', '.join(slugs)}")
        print("  处置口径见 docs/drift-triage.md")
    if unreachable:
        return 2
    # 5.4: malformed ledger rows fail the run with line numbers already
    # reported (never silent) — the operator fixes the row format.
    if MALFORMED_ROWS:
        print(f"\n❌ 台账存在 {len(MALFORMED_ROWS)} 行格式异常（行号见上），不静默跳过")
        return 1
    return 1 if (drift or leak or refs) else 0


if __name__ == "__main__":
    sys.exit(main())
