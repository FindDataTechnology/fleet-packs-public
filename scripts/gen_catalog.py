#!/usr/bin/env python3
"""条线能力目录生成器（openspec: add-meta-fleet-line / fleet-infra-catalog）。

为什么需要它：构造舰队写 `dataSources` / `allowTools` 时必须只引用**真的在册**的
server 与工具名。今天这条规则只写在 persona 里（「只用在册 server 名」），是一句
荣誉规则——2026-10-25 试点就是这么翻车的（`news_api` / `newsdata_io` 被校验闸抓出）。
本脚本把「在册」变成一份可失败的产物。

四条硬事实（2026-11-13 实测，决定了本脚本的形状）：

1. **条线不在 category 里**。47 个 manifest 中 23 个写 `category: fleet`、5 个
   `fleet-shared`、13 个没有 category——category 记的是「这是团队包」，不是「哪条线」。
   真正的线信号是 `fleet-packs/` 下的**一级目录**（wire/lex/erp/crossborder/…）。
   平台侧的 display 线（pack-taxonomy.js 的 LINES）是第三种分法，只用于展示，
   不可当组队依据。

2. **同一台服务器在不同形态下名字不同**：市场目录 `facet-market-mcp` / 部署基线
   `facet-market` / 打包形态 `actions-mcp` vs `actions`。所以 forms 各留一列，
   合并成一列就再也看不出「按错形态写名字」。

3. **只有市场目录带 tool_list**。快照 `registry-allowtools.json` 也带（22 条），
   但会过时。所以工具面分 `market` / `snapshot` / `none` 三档来源，并如实标。

4. **看图板面与记忆面是可派生的**：看板 = 某个 server 的 tool_list 里有
   `wire_artifact_dashboard_create`；记忆面 = 平台四轴（全局），线的差别在「本线
   有多少包声明了它」——这个数正是「造好了没人用」的度量。

用法：
    python3 fleet-packs/scripts/gen_catalog.py --out fleet-packs/shared/meta-catalog/catalog.json
    python3 fleet-packs/scripts/gen_catalog.py --offline --out /tmp/catalog.json   # 不碰网络
退出码：0=生成成功（允许带 gaps）；1=连本地制品都读不到（真错误）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # fleet-packs/
ROOT = REPO.parent                                      # finddata/
PAAS = ROOT / "paas"
DEFAULT_BASE = "https://facet.finddatatech.cloud"
DEFAULT_AUTHOR = "operator@example.com"

# 上游（每个都独立记 status，读不到就是 gap，不是静默略过）
MARKET_SNAPSHOT = PAAS / "market-catalog.json"

# 四份「兄弟仓输入」：**默认用随仓快照**（确定性），live 必须显式指定。
# 为什么：这四份原本只能从兄弟目录 <workspace>/paas 取，于是「能不能验目录」取决于你在哪
# checkout。2026-11-13 实测两种错法：①没有该邻居的 clone（/tmp）判不出 baseline 带入的
# 条目，把 cron/library/actions 诬告成手写增补；②有邻居但那个邻居是另一条分支的 worktree
# （/private/tmp/paas → /tmp/paas-mg，HEAD 17f4cea11），于是 taxonomy 走 live、其余走快照，
# 同一份目录的来源被环境掺着看。⇒ 默认路径必须与 checkout 位置无关。
# live 用法：`--paas-root <path>` 或 `FP_PAAS_ROOT=<path>`（要求四份输入齐，缺一即报错，
# 不静默退回快照）。快照存的是**数据本身**——taxonomy 存 categoryLines() 的输出而非 JS 源码，
# 免去在别人的检出里执行别人的代码；来源记在 inputs/_provenance.json（含源提交与逐文件 sha256）。
INPUT_SNAPSHOT = REPO / "shared" / "meta-catalog" / "inputs"
INPUT_PROVENANCE = INPUT_SNAPSHOT / "_provenance.json"

PAAS_INPUTS = {
    "baseline": ("mcp.json", "mcp.json"),
    "bundle": ("platform.bundle.json", "platform.bundle.json"),
    "allowtools": ("docs/fleet-templates/staging/registry-allowtools.json",
                   "registry-allowtools.json"),
    "taxonomy": ("lib/pack-taxonomy.js", "pack-taxonomy.category-lines.json"),
}


def _resolve_inputs(paas_root: str | Path | None) -> dict[str, tuple[Path, str]]:
    """返回 {键: (路径, source 标签)}。给了 paas_root 就必须齐，否则明说并退出。"""
    if paas_root:
        root = Path(paas_root).expanduser()
        missing = [rel for rel, _ in PAAS_INPUTS.values() if not (root / rel).exists()]
        if missing:
            raise SystemExit(f"--paas-root {root} 缺输入（不静默退回快照）：{missing}")
        return {k: (root / rel, str(root / rel)) for k, (rel, _snap) in PAAS_INPUTS.items()}
    return {k: (INPUT_SNAPSHOT / snap, f"input-snapshot/{snap}")
            for k, (_rel, snap) in PAAS_INPUTS.items()}


def apply_paas_root(paas_root: str | Path | None) -> None:
    """设定四个模块级输入（CLI 与环境变量都走这里）。"""
    global BASELINE, BASELINE_SRC, BUNDLE, BUNDLE_SRC
    global ALLOWTOOLS_SNAPSHOT, ALLOWTOOLS_SRC, TAXONOMY, TAXONOMY_SRC
    got = _resolve_inputs(paas_root)
    (BASELINE, BASELINE_SRC) = got["baseline"]
    (BUNDLE, BUNDLE_SRC) = got["bundle"]
    (ALLOWTOOLS_SNAPSHOT, ALLOWTOOLS_SRC) = got["allowtools"]
    (TAXONOMY, TAXONOMY_SRC) = got["taxonomy"]


apply_paas_root(os.environ.get("FP_PAAS_ROOT"))

# 注册处（mcp-gateway-registry）：**唯一带权威 tool_list 的来源**。
# 市场目录 `/api/mcp-catalog` 只给展示字段（name/displayName/description/
# endpoint/requiredGroups/annotation），**不带 tool_list**（2026-11-13 实测 25 条全部没有）。
# 所以工具面必须从注册处 `/api/servers?limit=500` 取；地址与凭据与 paas 生产一致
# （REGISTRY_URL=mcp.finddatatech.cloud，token 走 wanxing-fleet/registry_token）。
REGISTRY_URL_DEFAULT = "https://mcp.finddatatech.cloud"
REGISTRY_SECRET = ("fd-prod", "wanxing-fleet", "registry_token")

# 条线分组：一级目录 → 显示名 + 类别。**目录是真源**（见模块头 4.1）。
GROUPS: dict[str, dict] = {
    "wire": {"label": "柏讯 · 数据与情报", "kind": "line"},
    "lex": {"label": "识律 · 法律智能", "kind": "line"},
    "facet": {"label": "谦面 · 业务流", "kind": "line"},
    "wanxing": {"label": "萬星 · 智能体连接", "kind": "line"},
    "marketing": {"label": "营销", "kind": "line"},
    "crossborder": {"label": "跨境电商", "kind": "line"},
    "erp": {"label": "ERP 业务域", "kind": "line"},
    "hotel": {"label": "酒店", "kind": "vertical"},
    "flower": {"label": "花卉", "kind": "vertical"},
    "restaurant": {"label": "餐饮", "kind": "vertical"},
    "qicai": {"label": "启材（教育）", "kind": "vertical"},
    "shared": {"label": "共用层（跨线）", "kind": "shared"},
    "blueprints": {"label": "蓝图（构造面）", "kind": "blueprint"},
    "adopted": {"label": "采纳（第三方来源）", "kind": "adopted"},
    "tooling": {"label": "工具", "kind": "tooling"},
    "wire-suites-snapshot": {"label": "柏讯套件快照", "kind": "snapshot"},
}

BOARD_TOOL = "wire_artifact_dashboard_create"
MEMORY_AXES = ["store", "read", "write", "process"]


# ── 通用读取 ────────────────────────────────────────────────────────────────

def load_json(path: Path):
    """返回 (data, error)。缺失也算 error，不抛。"""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def market_token(env=os.environ) -> str:
    t = (env.get("FACET_INTERNAL_TOKEN") or "").strip()
    if t:
        return t
    try:
        out = subprocess.run(
            ["kubectl", "-n", "fd-prod", "get", "secret", "facet-secrets",
             "-o", "jsonpath={.data.FACET_INTERNAL_TOKEN}"],
            capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return base64.b64decode(out.stdout.strip()).decode()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _identity() -> str:
    raw = json.dumps({"email": DEFAULT_AUTHOR, "groups": ["creators", "platform"]}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _kubectl_secret(ns: str, name: str, key: str) -> str:
    try:
        out = subprocess.run(
            ["kubectl", "-n", ns, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}"],
            capture_output=True, text=True, timeout=20)
        if out.returncode == 0 and out.stdout.strip():
            return base64.b64decode(out.stdout.strip()).decode()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _path_to_name(p: str) -> str:
    """与 paas/registry-bridge.js 的 pathToName 逐字同源：/fd-info/ → fd-info。"""
    return "-".join([seg for seg in str(p or "").split("/") if seg])


def _tool_list_names(tool_list) -> list[str] | None:
    """与 paas/registry-bridge.js 的 toolListNames 同义：空/非法 ⇒ None（未知，不是零）。"""
    if not isinstance(tool_list, list):
        return None
    names = []
    for t in tool_list:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict) and isinstance(t.get("name"), str):
            names.append(t["name"])
    return names or None


def read_registry(offline: bool, env=os.environ) -> dict:
    """注册处 /api/servers 的 tool_list —— 工具面的权威来源。

    不带 tool_list 的市场目录只能证明"这台服务器在册"，不能证明"这个工具在册"；
    发布门的 `toolGateErrors` 也是拿注册处的 tool_list 比的，两边必须同源。
    """
    out = {"status": "unavailable", "source": "registry", "count": 0, "error": None,
           "servers": {}}
    if offline:
        out["error"] = "offline"
        return out

    base = (env.get("MARKET_REGISTRY_URL") or env.get("REGISTRY_URL")
            or REGISTRY_URL_DEFAULT).rstrip("/")
    tok = (env.get("MARKET_REGISTRY_TOKEN") or "").strip()
    if not tok:
        ns, name, key = REGISTRY_SECRET
        tok = _kubectl_secret(ns, name, key)
    if not tok:
        out["error"] = "no-token (MARKET_REGISTRY_TOKEN 未设且 kubectl 取不到 registry_token)"
        return out

    req = urllib.request.Request(f"{base}/api/servers?limit=500",
                                 headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out

    rows = body.get("servers") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    servers: dict[str, dict] = {}
    for s in rows:
        if not isinstance(s, dict) or s.get("is_enabled") is False:
            continue
        name = _path_to_name(s.get("path") or s.get("name") or "")
        if not name:
            continue
        surface = (s.get("metadata") or {}).get("surface") if isinstance(s.get("metadata"), dict) else None
        commercial = None
        counts_only = False
        if isinstance(surface, dict):
            if isinstance(surface.get("commercial_tools"), list):
                commercial = [str(t) for t in surface["commercial_tools"]]
            elif surface.get("tools_commercial") is not None:
                counts_only = True
        servers[name] = {
            "tools": _tool_list_names(s.get("tool_list")),
            "commercialTools": commercial,
            "surfaceCountsOnly": counts_only,
            "health": s.get("health_status"),
            "endpoint": s.get("mcp_endpoint") or s.get("endpoint"),
        }
    out.update(status="live", count=len(servers), servers=servers, url=base)
    return out


# ── 上游：市场目录 ──────────────────────────────────────────────────────────

def read_market(tok: str, offline: bool) -> dict:
    """线上市场目录（带 tool_list）。拿不到就退回快照，并如实记 status/source。"""
    out = {"status": "unavailable", "source": None, "count": 0, "error": None}
    if offline:
        out["error"] = "offline"
    elif not tok:
        out["error"] = "no-token (FACET_INTERNAL_TOKEN 未设且 kubectl 取不到)"
    else:
        req = urllib.request.Request(f"{DEFAULT_BASE.rstrip('/')}/api/mcp-catalog",
                                     headers={"x-market-token": tok})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
            rows = body.get("mcpServers") or body.get("servers") or []
            out.update(status="live", source="market", count=len(rows), servers=rows)
            return out
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"

    snap, err = load_json(MARKET_SNAPSHOT)
    if snap is None:
        out["error"] = (out["error"] or "") + f"; snapshot: {err}"
        return out
    rows = snap.get("mcpServers") or snap.get("servers") or []
    out.update(status="snapshot", source=str(MARKET_SNAPSHOT.relative_to(ROOT)),
               count=len(rows), servers=rows)
    return out


def _tools_of(row: dict) -> list[str] | None:
    """市场条目的工具面。没有声明 = None（未知），不是空集。"""
    declared = row.get("tool_list")
    if declared is None:
        declared = row.get("tools")
    if not isinstance(declared, list):
        return None
    out = []
    for t in declared:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            out.append(t["name"])
        elif isinstance(t, str):
            out.append(t)
    return out


# ── 上游：本地制品 ──────────────────────────────────────────────────────────

def read_local_packs() -> list[dict]:
    """扫 fleet-packs/**/manifest.json，取组（一级目录）、工具面引用、三支柱声明。"""
    packs = []
    for path in sorted(REPO.glob("*/**/manifest.json")):
        rel = path.relative_to(REPO)
        if rel.parts[0].startswith("."):
            continue
        data, err = load_json(path)
        if not isinstance(data, dict):
            continue
        group = rel.parts[0]
        servers = []
        for s in data.get("mcpServers") or []:
            if not isinstance(s, dict):
                continue
            name = s.get("registryName")
            if not isinstance(name, str) or not name:
                continue
            allow = s.get("allowTools")
            servers.append({"registryName": name,
                            "allowTools": sorted(allow) if isinstance(allow, list) else None})
        agents = data.get("agents") or []
        agent_leaves = []
        for a in agents:
            if not isinstance(a, dict):
                continue
            rt = (a.get("resources") or {}).get("mcpTools")
            if not isinstance(rt, dict):
                continue
            for server, tools in rt.items():
                if not isinstance(tools, list):
                    continue
                for t in tools:
                    if isinstance(t, str):
                        agent_leaves.append({"agentId": a.get("id"), "server": server, "tool": t})
        packs.append({
            "path": str(rel),
            "slug": path.parent.name,
            "group": group,
            "category": data.get("category"),
            "name": data.get("name"),
            "version": data.get("version"),
            "servers": servers,
            "agentToolLeaves": agent_leaves,
            "declaresMemory": isinstance(data.get("memory"), dict),
            "memoryAxes": sorted((data.get("memory") or {}).keys()) if isinstance(data.get("memory"), dict) else [],
            "declaresBoard": data.get("board") is not None,
            "agents": len(data.get("agents") or []),
            "agentsWithToolNarrowing": sum(
                1 for a in (data.get("agents") or [])
                if isinstance(a, dict) and isinstance(a.get("resources"), dict)
            ),
            "agentsWithMemoryGuidance": sum(
                1 for a in (data.get("agents") or [])
                if isinstance(a, dict) and isinstance((a.get("memory") or {}).get("guidance"), str)
            ),
        })
    return packs


def read_taxonomy() -> dict:
    """平台侧 category → display 线。**不复制这张表**；同级 paas 在时用 node 桥接取，
    没有该邻居时读随仓快照里的 categoryLines() 输出（status=ok，不是 live）。"""
    if not TAXONOMY.exists():
        return {"status": "unavailable", "error": f"{TAXONOMY.name} 不存在"}
    if TAXONOMY.suffix != ".js":
        data, err = load_json(TAXONOMY)
        if data is None:
            return {"status": "unavailable", "error": err}
        return {"status": "ok", "map": (data.get("map") if isinstance(data, dict) else None) or {}}
    try:
        out = subprocess.run(
            ["node", "-e",
             "import('./lib/pack-taxonomy.js')"
             ".then(m=>console.log(JSON.stringify(m.categoryLines())))"],
            cwd=str(PAAS), capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {"status": "unavailable", "error": (out.stderr or "").strip()[:300]}
        return {"status": "live", "map": json.loads(out.stdout)}
    except Exception as e:  # noqa: BLE001
        return {"status": "unavailable", "error": f"{type(e).__name__}: {e}"}


# ── 组装 ────────────────────────────────────────────────────────────────────

def build_servers(market: dict, registry: dict) -> dict:
    """名字 → {forms, tools, toolsSource, commercialTools?}。

    工具面优先级：**注册处 > 市场目录 > allowtools 快照**。理由是能力强度：
    注册处是发布门 `toolGateErrors` 同源的那份；市场目录（现网 25 条）根本不带
    tool_list；快照会过时且只覆盖 22 条。
    """
    servers: dict[str, dict] = {}

    def touch(name: str) -> dict:
        ent = servers.setdefault(name, {"forms": {}, "tools": None, "toolsSource": "none"})
        return ent

    # ① 注册处（权威工具面）
    for name, info in (registry.get("servers") or {}).items():
        ent = touch(name)
        ent["forms"]["registry"] = name
        if info.get("tools"):
            ent["tools"] = sorted(set(info["tools"]))
            ent["toolsSource"] = "registry"
        if info.get("commercialTools") is not None:
            ent["commercialTools"] = info["commercialTools"]
        if info.get("surfaceCountsOnly"):
            ent["surfaceCountsOnly"] = True
        if info.get("health"):
            ent["health"] = info["health"]

    # ② 市场目录形态（带 tool_list 时才算工具面；现网不带）
    for row in market.get("servers") or []:
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        ent = touch(name)
        ent["forms"]["market"] = name
        tools = _tools_of(row)
        if tools is not None and ent["tools"] is None:
            ent["tools"] = sorted(set(tools))
            ent["toolsSource"] = market.get("status")  # live | snapshot

    # ③ 部署基线形态（无工具面）
    baseline, _ = load_json(BASELINE)
    for name in ((baseline or {}).get("mcpServers") or {}):
        touch(name)["forms"]["baseline"] = name

    # ④ 打包形态（无工具面）
    bundle, _ = load_json(BUNDLE)
    for name in ((bundle or {}).get("mcpServers") or {}):
        touch(name)["forms"]["bundle"] = name

    # ⑤ allowtools 快照：只补「注册处与市场都没给工具面」的条目，来源标弱
    snap, _ = load_json(ALLOWTOOLS_SNAPSHOT)
    if isinstance(snap, dict):
        for name, tools in snap.items():
            if not isinstance(tools, list):
                continue
            ent = touch(name)
            ent["forms"]["allowtoolsSnapshot"] = name
            if ent["tools"] is None:
                ent["tools"] = sorted(set(tools))
                ent["toolsSource"] = "allowtools-snapshot"

    return servers


def attach_usage(servers: dict, packs: list[dict]) -> list[dict]:
    """给每个 server 补「谁在用、用了哪几个工具」，并返回「引用了但四源都查不到」的名字。

    这一步是必需的而不是装饰：`paas/market-catalog.json` 是旧快照，**缺 fd-open-data-mcp**
    （`check_drift.py` 里已记过这条），于是该 server 会只有工具面、没有任何 form 列，
    读起来像「未在册」——实际它被 3 个包在用。把「被引用」本身当成一条证据记下来，
    才不会把「快照缺条」误报成「名字不存在」。
    """
    unresolved: list[str] = []
    for p in packs:
        for s in p["servers"]:
            name = s["registryName"]
            ent = servers.setdefault(name, {"forms": {}, "tools": None, "toolsSource": "none"})
            slot = ent.setdefault("referencedBy", {}).setdefault(
                p["group"], {"packs": [], "allowTools": []})
            slot["packs"].append(p["slug"])
            if s["allowTools"]:
                slot["allowTools"] = sorted(set(slot["allowTools"]) | set(s["allowTools"]))

    for name, ent in servers.items():
        for slot in (ent.get("referencedBy") or {}).values():
            slot["packs"].sort()
        # 四源（market/baseline/bundle/allowtools）都没有 ⇒ 只有本地包在引用它
        if not ent["forms"] and not ent["tools"]:
            unresolved.append(name)
    return sorted(unresolved)


def alias_candidates(servers: dict) -> list[dict]:
    """同一台服务器的不同形态名。两种证据，分开报，不自动合并。

    ① 工具面完全相同 ⇒ 极可能同机；
    ② 名字只差 `-mcp` 后缀 ⇒ 极可能同机（`fleet-packs/README.md` 记的坑：
       `actions-mcp`(打包) / `actions`(基线)、`facet-market`(基线) / `facet-market-mcp`(市场)）。
    ②不能靠①兜住：allowtools 快照里 `facet-market` 记 publish、`facet-market-mcp` 记 validate，
    工具面因此不同，但它们是同一台服务器。所以两种证据都要，且都只作候选。
    """
    out: list[dict] = []
    by_tools: dict[tuple, list[str]] = {}
    for name, ent in servers.items():
        tools = ent.get("tools")
        if not tools:
            continue
        by_tools.setdefault(tuple(tools), []).append(name)
    for tools, names in by_tools.items():
        if len(names) > 1:
            out.append({"names": sorted(names), "evidence": f"identical {len(tools)}-tool face"})

    by_stem: dict[str, list[str]] = {}
    for name in servers:
        by_stem.setdefault(name[:-4] if name.endswith("-mcp") else name, []).append(name)
    tsuffixed = {tuple(sorted(v)) for v in by_stem.values() if len(v) > 1}
    for names in sorted(tsuffixed):
        out.append({"names": list(names), "evidence": "name differs only by -mcp suffix"})

    merged: dict[tuple, set] = {}
    for item in out:
        merged.setdefault(tuple(item["names"]), set()).add(item["evidence"])
    return [{"names": list(k), "evidence": " + ".join(sorted(v))}
            for k, v in sorted(merged.items())]


def build_groups(packs: list[dict], servers: dict) -> dict:
    """组 → 本组包/引用到的 server/工具面，以及三支柱使用率。"""
    groups: dict[str, dict] = {}
    for slug, meta in GROUPS.items():
        groups[slug] = {"label": meta["label"], "kind": meta["kind"],
                        "packs": [], "servers": {}}

    for p in packs:
        g = p["group"]
        if g not in groups:
            groups[g] = {"label": g, "kind": "unknown", "packs": [], "servers": {}}
        groups[g]["packs"].append({
            "slug": p["slug"], "name": p["name"], "version": p["version"],
            "category": p["category"], "declaresMemory": p["declaresMemory"],
            "declaresBoard": p["declaresBoard"],
            "agents": p["agents"],
            "agentsWithToolNarrowing": p["agentsWithToolNarrowing"],
        })
        for s in p["servers"]:
            slot = groups[g]["servers"].setdefault(
                s["registryName"], {"packs": [], "allowTools": []})
            slot["packs"].append(p["slug"])
            if s["allowTools"]:
                slot["allowTools"] = sorted(set(slot["allowTools"]) | set(s["allowTools"]))

    # 只留非空组，按包数降序
    out = {k: v for k, v in groups.items() if v["packs"]}
    for v in out.values():
        v["packs"].sort(key=lambda x: x["slug"])
        for s in v["servers"].values():
            s["packs"].sort()
        v["packCount"] = len(v["packs"])
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["packCount"]))


def check_declared_tools(packs: list[dict], servers: dict) -> dict:
    """每个包声明的工具叶子，是否真的在对应服务器的工具面里。

    三层结果，**绝不合并**（仓内既有教训：把「读不到」当成「不存在」会伪造证据）：
      · violation —— 服务器工具面已知，名字不在里面（真缺陷：运行时调不到，且不报错）；
      · unknown   —— 服务器工具面未知（none），无法判定，如实标而未取得；
      · 其余       —— 命中。

    为什么这条必须是目录的一部分而不是事后检查：2026-11-13 实测
    `wire_artifact_dashboard_create` 被 12 个 manifest 声明，而线上
    `wire-build-mcp` 的 15 个工具里根本没有它（注册处自报 healthy、
    last_checked 是当天）。发布门 `PACK_TOOL_FACE_GATE` 默认 `warn`，
    所以这种声明一路绿着上了架。
    """
    violations: list[dict] = []
    unknown: list[dict] = []
    checked = 0
    for p in packs:
        declared = []
        for s in p["servers"]:
            for t in (s["allowTools"] or []):
                declared.append((s["registryName"], t, "allowTools"))
        # 逐岗收窄也一并查（resources.mcpTools 与 allowTools 同级效力）
        for a in p.get("agentToolLeaves") or []:
            declared.append((a["server"], a["tool"], f"agents[{a['agentId']}]"))

        for server, tool, where in declared:
            ent = servers.get(server)
            if not ent:
                unknown.append({"pack": p["slug"], "server": server, "tool": tool,
                                "where": where, "why": "server 不在目录里"})
                continue
            face = ent.get("tools")
            if not face:
                unknown.append({"pack": p["slug"], "server": server, "tool": tool,
                                "where": where, "why": "该 server 工具面未知"})
                continue
            # 只有注册处自报的工具面才配判"不存在"。`allowtools-snapshot` 那份是
            # **白名单并集**，不是服务器的真实工具面——拿它判红就是拿自己的声明
            # 证伪自己的声明（2026-11-13 首跑 41 条里有 26 条正是这么来的）。
            if ent.get("toolsSource") != "registry":
                unknown.append({"pack": p["slug"], "server": server, "tool": tool,
                                "where": where,
                                "why": f"工具面来源为 {ent.get('toolsSource')}（弱证据），不作判定"})
                continue
            checked += 1
            if tool not in face:
                violations.append({"pack": p["slug"], "group": p["group"],
                                   "server": server, "tool": tool, "where": where,
                                   "faceSource": ent.get("toolsSource"),
                                   "faceSize": len(face)})
    violations.sort(key=lambda v: (v["pack"], v["server"], v["tool"]))
    unknown.sort(key=lambda v: (v["pack"], v["server"], v["tool"]))
    return {"checked": checked, "violations": violations, "unknown": unknown}


def build_surfaces(servers: dict, packs: list[dict]) -> dict:
    """看板面与记忆面：可派生的事实，而不是愿望。"""
    board_servers = sorted(n for n, e in servers.items()
                           if e.get("tools") and BOARD_TOOL in e["tools"])
    return {
        "board": {
            "tool": BOARD_TOOL,
            "servers": board_servers,
            "available": bool(board_servers),
            "note": ("没有任何 server 的工具面声明看板投稿工具" if not board_servers
                     else "以下 server 提供看板投稿面"),
            "packsDeclaringBoard": sorted(p["slug"] for p in packs if p["declaresBoard"]),
        },
        "memory": {
            "axes": MEMORY_AXES,
            "available": True,   # 平台四轴闭集已实现（pack-memory-declaration 已归档）
            "packsDeclaringMemory": sorted(p["slug"] for p in packs if p["declaresMemory"]),
            "packsDeclaringGuidance": sorted(
                p["slug"] for p in packs if p["agentsWithMemoryGuidance"] > 0),
        },
    }


def build_catalog(offline: bool = False, now: str | None = None) -> dict:
    tok = "" if offline else market_token()
    market = read_market(tok, offline)
    registry = read_registry(offline)
    packs = read_local_packs()
    taxonomy = read_taxonomy()
    servers = build_servers(market, registry)
    unresolved_refs = attach_usage(servers, packs)
    groups = build_groups(packs, servers)
    declared = check_declared_tools(packs, servers)

    baseline, base_err = load_json(BASELINE)
    bundle, bundle_err = load_json(BUNDLE)
    snap, snap_err = load_json(ALLOWTOOLS_SNAPSHOT)

    gaps = []
    if market["status"] != "live":
        gaps.append({"upstream": "market", "status": market["status"],
                     "error": market.get("error")})
    if registry["status"] != "live":
        gaps.append({"upstream": "registry", "status": registry["status"],
                     "error": registry.get("error")})
    for name, err in (("baseline", base_err), ("bundle", bundle_err),
                      ("allowtoolsSnapshot", snap_err)):
        if err:
            gaps.append({"upstream": name, "status": "unavailable", "error": err})
    if snap and market["status"] != "live":
        missing = sorted(set(snap) - set(servers))
        if missing:
            gaps.append({"upstream": "allowtoolsSnapshot", "status": "diverged",
                         "error": f"快照有而生成结果没有：{missing}"})
    if taxonomy.get("status") not in ("live", "ok"):
        gaps.append({"upstream": "taxonomy", "status": "unavailable",
                     "error": taxonomy.get("error")})
    if unresolved_refs:
        # 引用名在四个上游都查不到：市场在线时是真笔误；退回快照时只配"可能缺失"。
        gaps.append({
            "upstream": "localPacks", "status": "ready" if market["status"] == "live" else "unresolved",
            "error": f"被引用于四个上游都查不到的名字：{unresolved_refs}",
        })
    if declared["violations"]:
        gaps.append({
            "upstream": "declaredTools", "status": "not-exposed",
            "error": (f"{len(declared['violations'])} 条声明的工具不在该服务器工具面里"
                      f"（{len({(v['server'], v['tool']) for v in declared['violations']})} 个不同工具）"),
        })

    return {
        "generatedAt": now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "fleet-packs/scripts/gen_catalog.py",
        "schemaVersion": 1,
        "sources": {
            "registry": {"status": registry["status"], "source": registry.get("url")
                         or f"{REGISTRY_SECRET[0]}/{REGISTRY_SECRET[1]}:{REGISTRY_SECRET[2]}",
                         "count": registry["count"], "error": registry.get("error"),
                         "hasToolList": True},
            "market": {k: market.get(k) for k in ("status", "source", "count", "error")},
            "marketHasToolList": any(
                _tools_of(r) is not None for r in (market.get("servers") or [])
            ),
            "baseline": {"status": "ok" if baseline else "unavailable",
                         "source": BASELINE_SRC,
                         "count": len((baseline or {}).get("mcpServers") or {})},
            "bundle": {"status": "ok" if bundle else "unavailable",
                       "source": BUNDLE_SRC,
                       "count": len((bundle or {}).get("mcpServers") or {})},
            "allowtoolsSnapshot": {"status": "ok" if isinstance(snap, dict) else "unavailable",
                                   "source": ALLOWTOOLS_SRC,
                                   "count": len(snap or {})},
            "taxonomy": {"status": taxonomy.get("status"),
                         "source": TAXONOMY_SRC,
                         "count": len(taxonomy.get("map") or {})},
        },
        "lineNote": ("条线分组以 fleet-packs/ 一级目录为准：category 里 23 个包写 "
                     "'fleet'，记的是「团队包」而非「哪条线」（2026-11-13 实测）。"),
        "categoryToDisplayLine": taxonomy.get("map") or {},
        "groups": groups,
        "servers": dict(sorted(servers.items())),
        "aliasCandidates": alias_candidates(servers),
        "declaredNotExposed": declared,
        "snapshotCrossCheck": cross_check_snapshot(servers),
        "unresolvedRefs": unresolved_refs,
        "surfaces": build_surfaces(servers, packs),
        "packs": [{k: p[k] for k in ("slug", "path", "group", "category", "version",
                                     "declaresMemory", "declaresBoard", "agents",
                                     "agentsWithToolNarrowing",
                                     "agentsWithMemoryGuidance")} for p in packs],
        "gaps": gaps,
    }


def cross_check_snapshot(servers: dict) -> dict:
    """与既有 22 条 allowtools 快照对账：覆盖 + 逐工具差异。

    快照的语义是**白名单并集**（某台服务器被包允许用过哪些工具），不是服务器的
    完整工具面。所以「快照有、注册处没有」只能当**待复核**，不能直接判红；反过来
    「注册处有、快照没有」是正常扩面。两侧都如实列出，供人判。
    """
    snap, err = load_json(ALLOWTOOLS_SNAPSHOT)
    if not isinstance(snap, dict):
        return {"status": "unavailable", "error": err, "count": 0, "covered": 0,
                "missingFromCatalog": [], "toolDiffs": []}
    covered = sorted(set(snap) & set(servers))
    missing = sorted(set(snap) - set(servers))
    diffs = []
    for name in covered:
        declared = set(t for t in (snap.get(name) or []) if isinstance(t, str))
        face = servers[name].get("tools")
        if not face or servers[name].get("toolsSource") != "registry":
            continue  # 弱证据不算差异
        face = set(face)
        if declared - face:
            diffs.append({"server": name,
                          "snapshotNotInRegistryFace": sorted(declared - face),
                          "registryFaceSize": len(face)})
    diffs.sort(key=lambda d: -len(d["snapshotNotInRegistryFace"]))
    return {"status": "ok", "count": len(snap), "covered": len(covered),
            "missingFromCatalog": missing, "toolDiffs": diffs,
            "basis": "只有注册处自报的工具面参与差异判定"}


def _snapshot_integrity(max_age_days: int,
                        snapshot_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """随仓输入的完整性：按 _provenance.json 记的 sha256/字节数逐文件复核。

    为什么这算硬断言：目录的确定性全部建立在「默认只读这几份随仓快照」这个前提上。
    前提被手改就破了——改一份 mcp.json 与手改 catalog.json 是**同一类罪**（绕过四源），
    只是症状更隐蔽：目录看上去仍是从源生成的，实际源被换过。出处文件既然记了每份的
    sha256 前 16 位与字节数，就不用靠自觉。

    `snapshot_dir` 只给自检用（造一份假的快照目录去注入），正式路径不传。
    """
    snap_dir = snapshot_dir or INPUT_SNAPSHOT
    errors: list[str] = []
    warnings: list[str] = []
    prov, err = load_json(snap_dir / "_provenance.json")
    if prov is None:
        return ([f"快照出处读不到：{snap_dir / '_provenance.json'} ({err})"
                 f"——无法证明随仓输入没被手改"], warnings)

    for name, meta in sorted((prov.get("files") or {}).items()):
        p = snap_dir / name
        if not p.exists():
            errors.append(f"快照缺文件：{name}（_provenance.json 有登记）——默认输入路径会读不到")
            continue
        blob = p.read_bytes()
        want_sha = str(meta.get("sha256") or "")
        got_sha = hashlib.sha256(blob).hexdigest()[:16]
        if want_sha and got_sha != want_sha:
            errors.append(f"快照被改过：{name} sha256 {got_sha} ≠ 出处记的 {want_sha}"
                          f"——换输入请连同 _provenance.json 一起重采，别只改文件")
        want_bytes = meta.get("bytes")
        if isinstance(want_bytes, int) and len(blob) != want_bytes:
            errors.append(f"快照字节数不符：{name} {len(blob)} ≠ 出处记的 {want_bytes}")

    cap = str(prov.get("capturedAt") or "")
    try:
        cap_dt = dt.datetime.strptime(cap, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        age = (dt.datetime.now(dt.timezone.utc) - cap_dt).days
        if age > max_age_days:
            warnings.append(
                f"随仓快照已 {age} 天未重采（阈值 {max_age_days} 天；源 "
                f"{prov.get('sourceRepo')}@{str(prov.get('sourceCommit') or '')[:9]}）"
                f"——目录反映的是那时的输入，重采需要 paas 检出")
    except Exception:  # noqa: BLE001
        warnings.append(f"快照 capturedAt 无法解析（{cap!r}）——无法说明它有多新")
    return (errors, warnings)


def verify_catalog(path: Path, max_age_days: int = 14, offline: bool = False,
                   fresh: dict | None = None) -> dict:
    """对已落盘的目录做三条硬断言 + 一条漂移告警。

    硬断言（不通过 ⇒ 退出码 1）：
      A 手写增补：目录里出现重新生成不出来的 server / group / pack ⇒ 指名，判红。
        理由：目录是**生成物**，手改一条就等于绕过四源，且下次重生成会被悄悄冲掉。
      B 陈旧：generatedAt 超过阈值 ⇒ 判红并给天数。判据必须来自它的生成时间，
        不能来自"我觉得还行"。
      B2 随仓快照被手改：与 _provenance.json 记的 sha256/字节数不符 ⇒ 指名，判红。
        理由见 _snapshot_integrity 的 docstring：默认输入路径的前提不能被悄悄换掉。
    告警（不判红）：
      C 工具面漂移：同名 server 的工具集合变了 ⇒ 列差异。这是真变化（上游加/删了
        工具），处置是重新生成，不是修目录。
    """
    disk, err = load_json(path)
    if disk is None:
        return {"ok": False, "errors": [f"目录读不到：{path} ({err})"], "warnings": [], "drift": []}

    fresh_cat = fresh if fresh is not None else build_catalog(offline=offline)
    errors: list[str] = []
    warnings: list[str] = []
    drift: list[dict] = []

    # 0 随仓输入没被手改（硬断言）——确定性默认路径的前提
    snap_errors, snap_warnings = _snapshot_integrity(max_age_days)
    errors.extend(snap_errors)
    warnings.extend(snap_warnings)

    # A 手写增补（三个维度）
    # 前提：比较的两份目录必须来自同等强度的来源。用弱来源（离线/无 token）去校验
    # 强来源（在线注册处）生成的目录，会把「只在注册处出现的 server」误判成手写增补
    # ——2026-11-13 自检首跑就是这么误报 3 台的（fd-source-probe / fd-text-core /
    # law-bench）。仓内既有纪律：弱证据不配判红。
    #
    # 六个来源全部要查，不能只查 registry/market（2026-11-13 新 clone 实测的误判）：
    # 本生成器按 `<workspace>/fleet-packs/` 与兄弟目录 `<workspace>/paas/` 的布局取
    # baseline / bundle / allowtoolsSnapshot / taxonomy（ROOT = REPO.parent）。换到
    # 任一没有这个邻居的检出（例如 `git clone` 到 /tmp），这四源全降级，于是盘上
    # 由 baseline 带进来的 `cron` / `library` / `actions` 等会被判成「手写增补」而判红；
    # 而在作者机器上因为 /Users/<u>/paas 是指向 finddata/paas 的软链，同一份目录
    # 一分钟内可以绿、可以红——判据不稳定本身就是错的。
    disk_src = (disk.get("sources") or {})
    fresh_src = (fresh_cat.get("sources") or {})
    degraded: list[str] = []
    for key in ("registry", "market", "baseline", "bundle", "allowtoolsSnapshot", "taxonomy"):
        was = (disk_src.get(key) or {}).get("status")
        now = (fresh_src.get(key) or {}).get("status")
        if was in ("live", "ok") and now not in ("live", "ok"):
            hint = ""
            if key in ("baseline", "bundle", "allowtoolsSnapshot", "taxonomy") and not PAAS.exists():
                hint = f"（找不到同级 {PAAS} 检出——本生成器要求 <workspace>/fleet-packs 与 <workspace>/paas 并列）"
            degraded.append(f"{key}: 生成时 {was} / 校验时 {now}{hint}")
    downgrade = bool(degraded)

    def flag(msg: str) -> None:
        (warnings if downgrade else errors).append(
            msg if not downgrade else msg + f"（本次校验来源更弱，降级为告警：{'; '.join(degraded)}）")

    for section in ("servers", "groups"):
        extra = sorted(set((disk.get(section) or {})) - set(fresh_cat.get(section) or {}))
        missing = sorted(set(fresh_cat.get(section) or {}) - set((disk.get(section) or {})))
        if extra:
            flag(f"{section} 里出现重新生成不出来的条目（手写增补）：{extra}")
        if missing:
            warnings.append(f"{section} 缺少重新生成得到的条目（可能生成后源变了）：{missing}")

    disk_packs = {p.get("slug") for p in (disk.get("packs") or []) if isinstance(p, dict)}
    fresh_packs = {p.get("slug") for p in (fresh_cat.get("packs") or [])}
    extra_packs = sorted(disk_packs - fresh_packs)
    if extra_packs:
        flag(f"packs 里出现重新生成不出来的包：{extra_packs}")

    # B 陈旧
    gen = str(disk.get("generatedAt") or "")
    try:
        gen_dt = dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        age_days = (dt.datetime.now(dt.timezone.utc) - gen_dt).days
    except Exception:  # noqa: BLE001
        errors.append(f"generatedAt 无法解析（{gen!r}）——无法证明不陈旧")
        age_days = None
    if age_days is not None and age_days > max_age_days:
        errors.append(f"目录已陈旧：生成于 {age_days} 天前（阈值 {max_age_days} 天）——请重新生成")

    # C 工具面漂移（告警）
    # 前提同样要有：只有在生成时与校验时**两边都拿到注册处实况**，比出来的差异才叫
    # 「上游加了/删了工具」。注册处本次不可达时，fresh 侧的工具面退到 allowtools-snapshot
    # 之类的弱来源，逐条比出来的是「这次没连上」，不是漂移——2026-11-13 在无凭据无网的
    # 干净 clone 里实测：会刷出 16 条这种假漂移（registry → allowtools-snapshot），
    # 把真变化淹掉。弱证据不配判红，也不配当告警刷屏：这里只留一条说明。
    registry_live = ((disk_src.get("registry") or {}).get("status") == "live"
                     and (fresh_src.get("registry") or {}).get("status") == "live")
    if not registry_live:
        warnings.append("工具面漂移未比对：注册处本次不可达（fresh registry != live），"
                        "不是「无漂移」")
    for name, ent in sorted((disk.get("servers") or {}).items()):
        if not registry_live:
            break
        fresh_ent = (fresh_cat.get("servers") or {}).get(name)
        if not fresh_ent:
            continue
        was = set(ent.get("tools") or [])
        now = set(fresh_ent.get("tools") or [])
        if was != now:
            drift.append({"server": name, "added": sorted(now - was), "removed": sorted(was - now),
                          "wasSource": ent.get("toolsSource"),
                          "nowSource": fresh_ent.get("toolsSource")})

    return {"ok": not errors, "errors": errors, "warnings": warnings, "drift": drift,
            "ageDays": age_days, "generatedAt": gen}


def self_test(base_catalog: Path | None = None, offline: bool = False) -> int:
    """证明三条硬断言都能红，然后证明干净副本能绿。

    与仓内既有纪律一致：**注入脚本自己要先证明能红**。不做这一步，`--verify`
    全绿只能说明它没被触发，不能说明它有效。

    基线由本函数**按当前模式现造**，而不是拿磁盘上那份——否则「在线生成的目录 vs
    离线校验」会因为来源强度不同而把注入项一起降级成告警（2026-11-13 实测：先降级
    过头导致负向对照失效）。
    """
    import tempfile

    pristine = build_catalog(offline=offline)
    if not pristine.get("packs") or not pristine.get("servers"):
        print("✗ 自检无法生成基线目录（读不到本地制品）", file=sys.stderr)
        return 2

    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="fd-catalog-selftest-") as td:
        tmp = Path(td)

        # ③-前置 干净副本（同模式现造，来源强度一致）
        p3 = tmp / "clean.json"
        p3.write_text(json.dumps(pristine, ensure_ascii=False), encoding="utf-8")
        v3 = verify_catalog(p3, offline=offline, fresh=pristine)

        # ① 手写增补：塞一个不存在的 server
        injected = json.loads(json.dumps(pristine))
        injected["servers"]["ghost-server-mcp"] = {"forms": {}, "tools": None,
                                                   "toolsSource": "none"}
        p1 = tmp / "handwritten.json"
        p1.write_text(json.dumps(injected, ensure_ascii=False), encoding="utf-8")
        v1 = verify_catalog(p1, offline=offline, fresh=pristine)
        results.append(("手写增补必须判红并指名", not v1["ok"],
                        next((e for e in v1["errors"] if "ghost-server-mcp" in e), "未指名注入条目")))

        # ② 陈旧：把生成时间往前拨 40 天
        backdated = json.loads(json.dumps(pristine))
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)
        backdated["generatedAt"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        p2 = tmp / "stale.json"
        p2.write_text(json.dumps(backdated, ensure_ascii=False), encoding="utf-8")
        v2 = verify_catalog(p2, offline=offline, fresh=pristine)
        results.append(("陈旧必须判红并给天数", not v2["ok"],
                        next((e for e in v2["errors"] if "陈旧" in e), "未报陈旧")))

        # ③ 随仓快照被手改：出处记的 sha256/字节数与实际不符 ⇒ 判红并指名
        fake_snap = tmp / "inputs"
        fake_snap.mkdir()
        (fake_snap / "mcp.json").write_text('{"mcpServers":{"tampered":{}}}', encoding="utf-8")
        (fake_snap / "_provenance.json").write_text(json.dumps({
            "capturedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sourceRepo": "paas", "sourceCommit": "0" * 40,
            "files": {"mcp.json": {"bytes": 999, "sha256": "0" * 16}},
        }), encoding="utf-8")
        snap_errs, _snap_warns = _snapshot_integrity(14, snapshot_dir=fake_snap)
        results.append(("快照被手改必须判红并指名", bool(snap_errs)
                        and any("mcp.json" in e for e in snap_errs),
                        next((e for e in snap_errs if "mcp.json" in e), "未判红")))

        results.append(("干净副本必须绿", v3["ok"], "；".join(v3["errors"]) or "ok"))

    ok = True
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name} — {detail}")
        ok = ok and passed
    print(f"self-test {'绿' if ok else '红'} ({sum(1 for _, p, _ in results if p)}/{len(results)})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成条线能力目录")
    ap.add_argument("--out", default=str(REPO / "shared" / "meta-catalog" / "catalog.json"))
    ap.add_argument("--offline", action="store_true", help="不碰网络（市场退回快照）")
    ap.add_argument("--print-gaps", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="校验已落盘目录（手写增补 / 陈旧 / 漂移），不重写文件")
    ap.add_argument("--self-test", action="store_true",
                    help="证明两条硬断言能红、干净副本能绿")
    ap.add_argument("--max-age-days", type=int, default=14, help="陈旧阈值（默认 14 天）")
    ap.add_argument("--paas-root", default=None,
                    help="改用该 paas 检出里的四份新鲜输入（默认用随仓快照；等价于 FP_PAAS_ROOT）")
    args = ap.parse_args(argv)

    if args.paas_root:
        apply_paas_root(args.paas_root)

    if args.self_test:
        return self_test(Path(args.out), offline=args.offline)

    if args.verify:
        v = verify_catalog(Path(args.out), max_age_days=args.max_age_days, offline=args.offline)
        print(f"verify {args.out}")
        print(f"  generatedAt={v.get('generatedAt')} ageDays={v.get('ageDays')} "
              f"(阈值 {args.max_age_days} 天)")
        for e in v["errors"]:
            print(f"  ✗ {e}")
        for w in v["warnings"]:
            print(f"  ⚠ {w}")
        for d in v["drift"]:
            print(f"  ⚠ 漂移 {d['server']}: +{d['added']} -{d['removed']} "
                  f"({d['wasSource']} → {d['nowSource']})")
        print(f"  {'绿' if v['ok'] else '红'}（errors={len(v['errors'])} "
              f"warnings={len(v['warnings'])} drift={len(v['drift'])}）")
        return 0 if v["ok"] else 1

    cat = build_catalog(offline=args.offline)
    if not cat["packs"]:
        print("✗ 读不到任何本地 manifest——目录无从生成", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    s = cat["sources"]
    print(f"catalog → {out}")
    print(f"  registry={s['registry']['status']}({s['registry']['count']},toolList={s['registry']['hasToolList']}) "
          f"market={s['market']['status']}({s['market']['count']},toolList={s['marketHasToolList']}) "
          f"baseline={s['baseline']['count']} bundle={s['bundle']['count']} "
          f"allowtools={s['allowtoolsSnapshot']['count']}")
    by_src: dict[str, int] = {}
    for e in cat["servers"].values():
        by_src[e["toolsSource"]] = by_src.get(e["toolsSource"], 0) + 1
    print(f"  toolFaces by source: {dict(sorted(by_src.items(), key=lambda kv: -kv[1]))}")
    print(f"  groups={len(cat['groups'])} servers={len(cat['servers'])} "
          f"packs={len(cat['packs'])} aliases={len(cat['aliasCandidates'])}")
    cov = s["allowtoolsSnapshot"]["count"] and (
        len(set(cat["servers"]) & set((load_json(ALLOWTOOLS_SNAPSHOT)[0] or {}))) )
    if cov is not None and s["allowtoolsSnapshot"]["count"]:
        print(f"  allowtools 快照覆盖 {cov}/{s['allowtoolsSnapshot']['count']}")
    xc = cat.get("snapshotCrossCheck") or {}
    if xc.get("status") == "ok" and xc.get("toolDiffs"):
        print(f"  快照与注册处工具面差异 {len(xc['toolDiffs'])} 台：")
        for d in xc["toolDiffs"][:6]:
            print(f"    ~ {d['server']}: 快照有而注册处面无 {d['snapshotNotInRegistryFace']}")
    print(f"  board={cat['surfaces']['board']['available']} "
          f"memoryDeclared={len(cat['surfaces']['memory']['packsDeclaringMemory'])}"
          f"/{len(cat['packs'])}")
    dn = cat["declaredNotExposed"]
    print(f"  declaredTools checked={dn['checked']} "
          f"violations={len(dn['violations'])} unknown={len(dn['unknown'])}")
    if dn["violations"]:
        by_tool: dict[tuple, list[str]] = {}
        for v in dn["violations"]:
            by_tool.setdefault((v["server"], v["tool"]), []).append(v["pack"])
        for (server, tool), packs_ in sorted(by_tool.items()):
            print(f"    ✗ {server}::{tool}  ({len(packs_)} packs: {', '.join(packs_[:4])}"
                  f"{'…' if len(packs_) > 4 else ''})")
    if cat["gaps"]:
        print(f"  gaps={len(cat['gaps'])}" + ("（--print-gaps 看详情）" if not args.print_gaps else ""))
        if args.print_gaps:
            for g in cat["gaps"]:
                print(f"    - {g['upstream']}: {g['status']} — {g['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
