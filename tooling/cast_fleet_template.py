r"""Cast a fleet template pack (fleet-packs convention) into a publishable manifest.

舰包目录约定（每舰一目录，例 fleet-packs/lex/<slug>/）：

  template.json  清单真源：agents[]（岗位 persona）+ fleet{members,orchestration}
                 + _casting.packDependencies（target.packId 用 "{{PACK_ID:<依赖名>}}" 占位）
  cast.json      包壳：{name, description, tags, mcpServers, notes,
                 visibility?="public", category?="fleet"}

packId 台账：fleet-packs/README.md §packId 台账（竖表行「| <pack-name> | <packId> |」，
kebab-case 包名）。占位缺依赖 / 台账缺行 → fail loud（绝不带占位上架）。

占位解析兼容仓库里并存的历史形态（lex 三舰首铸=自指占位，wire 首舰=deps 值已解析、
甚至有包着 packId 的占位）：deps 值可为 packId / 占位符 / 自指占位，一律归一。

产物：manifest.json（sibling；--check 模式不改盘，只对比既有产物是否一致——回归用）。
校验：写盘后自动跑两道发布前校验——paas parseFleetTemplate（成员/环/cron/边）+
scripts/validate_manifest.py（真 validatePackManifest，PACK_MAX_AGENTS 已透传）。
任一失败退出码 1。

用法：
  python3 cast_fleet_template.py <fleet-dir>            # 铸造 + 校验
  python3 cast_fleet_template.py <fleet-dir> --check    # 只对比既有 manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

LEDGER_NAME = "README.md"
# 仓根的识别标志：README 台账 + 一个只属于本仓的脚本。**不认目录名**——
# 本仓可能被 clone 成任意名字，也可能是工作区之外的 worktree；早先按
# `fleet-packs/README.md` 上溯，在 `git worktree add /tmp/fp-x` 里直接
# FileNotFoundError: '/fleet-packs/README.md'（上溯撞到文件系统根）。
REPO_MARKERS = ("scripts/gen_catalog.py", "scripts/check_drift.py")
ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`")
PLACEHOLDER_RE = re.compile(r"\{\{PACK_ID:([A-Za-z0-9_-]+)\}\}")

ledger: dict[str, str] = {}


def _find_repo_root(start: Path) -> Path:
    """从 start 向上找仓根：README + 本仓标志文件齐备的第一层。"""
    cur = start
    while True:
        if (cur / LEDGER_NAME).exists() and all((cur / m).exists() for m in REPO_MARKERS):
            return cur
        if cur == cur.parent:
            raise SystemExit(f"找不到 fleet-packs 仓根（从 {start} 上溯到 / 都没有 "
                             f"{LEDGER_NAME} + {REPO_MARKERS}）——用 --repo-root 显式指定")
        cur = cur.parent


def parse_ledger(repo_root: Path) -> dict[str, str]:
    path = repo_root / LEDGER_NAME
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            out.setdefault(m.group(1), m.group(2))
    if not out:
        raise SystemExit(f"ledger empty: {path}")
    return out


def resolve_dep(name: str, deps: dict[str, str]) -> str:
    """packId for a declared dependency. deps 值三种形态归一：
    裸 packId / 包名占位符 / 自指占位符。环与缺口 fail loud。"""
    seen: set[str] = set()
    cur = name
    while cur not in seen:
        seen.add(cur)
        val = deps.get(cur)
        if val is None:  # cur 不是依赖名 → 必须是台账在册包名
            if cur in ledger:
                return ledger[cur]
            raise SystemExit(f"dep '{name}' unresolved at '{cur}' — 不在台账，上架后回填再铸")
        m = PLACEHOLDER_RE.fullmatch(val)
        nxt = m.group(1) if m else val
        if nxt in ledger:  # 值是台账包名（或其占位）→ 直接取台账
            return ledger[nxt]
        if nxt == cur:
            # 依赖名与占位名相同的自引用，而台账里没有它 ⇒ 这个包还没铸造。
            # 原实现会在这里绕一圈报 "dependency cycle"，把「未铸造」误诊成「配置成环」，
            # 让人去改本来没错的依赖声明。直接说清缺什么。
            raise SystemExit(
                f"依赖 '{cur}' 的 packId 未铸造：先在 README §packId 台账回填 "
                f"`| {cur} | <packId> | v1 | <源目录> |` 再铸（这不是依赖成环）")
        if m:  # 还包着一层占位 → 继续解包
            cur = nxt
            continue
        return nxt  # 裸 packId → 完成
    raise SystemExit(f"dependency cycle resolving '{name}'")


def resolve_placeholder(name: str, resolved: dict[str, str]) -> str:
    """fleet/agents 里一个 {{PACK_ID:X}} 占位的落点：
    ① 声明过的依赖名 → 解析值；② 台账在册包名 → 台账值；
    ③ 已解析的 packId 本身（历史形态） → 原样；④ 其余 fail loud。"""
    if name in resolved:
        return resolved[name]
    if name in ledger:
        return ledger[name]
    if name in set(resolved.values()):
        return name
    raise SystemExit(f"placeholder {{PACK_ID:{name}}} 未声明也不在台账 — 先补 _casting.packDependencies 并回填台账")


def build_manifest(fleet_dir: Path) -> dict:
    tpl = json.loads((fleet_dir / "template.json").read_text(encoding="utf-8"))
    cast = json.loads((fleet_dir / "cast.json").read_text(encoding="utf-8"))
    deps: dict[str, str] = (tpl.get("_casting") or {}).get("packDependencies") or {}
    # 空依赖=自包含舰队（红线级纪律随 persona/技能自带的单岗舰）——合法形态，
    # 不再硬退场；{{PACK_ID:<自身 slug>}} 自引用走机器台账解析。
    if not deps:
        print("  packDependencies: 无（自包含舰队，纪律随模板自带）")
    resolved = {k: resolve_dep(k, deps) for k in deps}

    def sub(obj):
        if isinstance(obj, str):
            return PLACEHOLDER_RE.sub(lambda m: resolve_placeholder(m.group(1), resolved), obj)
        if isinstance(obj, list):
            return [sub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: sub(v) for k, v in obj.items()}
        return obj

    manifest = {
        "name": cast["name"],
        "description": cast["description"],
        "tags": cast["tags"],
        "visibility": cast.get("visibility", "public"),
        "category": cast.get("category", "fleet"),
        # 模板可内联技能（pack-locale-supply 对账：市场版已带 2–3 个技能，
        # 源侧此前固定空数组，重铸会把市场技能冲掉）。
        "skills": sub(tpl.get("skills", [])),
        "agents": sub(tpl["agents"]),
        "mcpServers": cast["mcpServers"],
        "notes": cast.get("notes", ""),
        "_casting": {
            "packDependencies": resolved,
            "note": (tpl.get("_casting") or {}).get("note", ""),
        },
    }
    # fleet 段只在舰队模板里有；岗位包（roles pack）没有 fleet，则不带该键。
    if tpl.get("fleet") is not None:
        manifest["fleet"] = sub(tpl["fleet"])
    # 三支柱与验收段必须随包走（add-pack-console / add-pack-memory-declaration /
    # add-team-release-pillars）：此前 cast 只搬三桶 + fleet，于是市场版丢掉
    # console（团队没有控制台）、memory 四轴、acceptance 与 monitoring——源里有、
    # 装上是空。缺省不写，保持「未声明则逐字节不变」。
    for key in ("console", "memory", "board", "acceptance", "monitoring"):
        section = tpl.get(key, cast.get(key))
        if section is not None:
            manifest[key] = sub(section)
    # 可选顶层 i18n 副轨（cast.json "i18n"：locale→显示字段覆写，pack-i18n-locale-tracks 契约）。
    # 市场 11 枚现行包带 en 副轨——不透传则重铸即丢英文显示层，与「源=市场真值」不变量冲突。
    if cast.get("i18n"):
        manifest["i18n"] = cast["i18n"]
    leftover = PLACEHOLDER_RE.search(json.dumps(manifest["agents"]) + json.dumps(manifest.get("fleet") or {}))
    if leftover:
        raise SystemExit(f"unresolved placeholder survived casting: {leftover.group(0)}")
    # 可选剥离（cast.json "strip": [...]）——兼容两代发布形态：
    #   "agentDescription"：agents 不带 description（wire 情报舰 v5 形态）
    #   "casting"：产物不带 _casting 段（同上；新包默认保留，追溯性更好）
    strip = cast.get("strip") or []
    if "agentDescription" in strip:
        for a in manifest["agents"]:
            a.pop("description", None)
    if "casting" in strip:
        manifest.pop("_casting", None)
    return manifest


def _registry_names() -> set[str] | None:
    """在册 MCP 服务器名集合；取不到返回 None（由调用方决定硬拦还是告警）。

    顺序：① 环境变量 ``FD_REGISTRY_SERVERS_JSON`` 指向的本地快照 →
    ② 活册 ``https://mcp.finddatatech.cloud/api/servers?limit=500``
    （token 取 ``MARKET_REGISTRY_TOKEN`` / ``FD_REGISTRY_TOKEN``）。只读，无副作用。
    """
    snapshot = os.environ.get("FD_REGISTRY_SERVERS_JSON")
    if snapshot and Path(snapshot).exists():
        try:
            payload = json.loads(Path(snapshot).read_text(encoding="utf-8"))
            rows = payload.get("servers") if isinstance(payload, dict) else payload
            names = {str(r.get("path", "")).strip("/") for r in rows if r.get("path")}
            if names:
                return names
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    token = os.environ.get("MARKET_REGISTRY_TOKEN") or os.environ.get("FD_REGISTRY_TOKEN")
    if not token:
        return None
    try:
        req = urllib.request.Request(
            "https://mcp.finddatatech.cloud/api/servers?limit=500",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (固定 https 主机)
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 取不到在册表由调用方决定
        return None
    rows = payload.get("servers") if isinstance(payload, dict) else payload
    try:
        return {str(r.get("path", "")).strip("/") for r in rows if r.get("path")}
    except (AttributeError, TypeError):
        return None


def check_registry_names(mcp_servers: list[dict], *, allow_offline_miss: bool) -> None:
    """构造闸：``mcpServers[].registryName`` 必须是在册 MCP 服务器名。

    为什么要有这道闸：``validatePackManifest`` 与平台发布闸都只校验 registryName 的
    **形状**（正则），实测一个不存在的名字会以「本地绑定」豁免静默放过；而岗位一旦
    真跑就会 0 工具可用。蓝图里写幻觉源必须在构造期被拦下（spec
    restaurant-line-fleets：「registryName 不在册要拦」）。

    取不到在册表时：默认**硬拦**（fail-closed，宁可不铸也不铸幻觉源）；
    ``allow_offline_miss=True``（--no-registry-check）显式放行，并在输出里留痕。
    """
    wanted = [str(s.get("registryName", "")).strip() for s in mcp_servers]
    wanted = [w for w in wanted if w]
    if not wanted:
        return
    names = _registry_names()
    if names is None:
        if allow_offline_miss:
            print("  registryName check: SKIPPED（--no-registry-check；未取得在册表）")
            return
        raise SystemExit(
            "registryName check FAIL: 未取得在册 MCP 表（设 MARKET_REGISTRY_TOKEN 或 "
            "FD_REGISTRY_SERVERS_JSON 快照），无法确认 "
            f"{wanted} 是否在册 —— fail-closed；确要离线铸造请加 --no-registry-check")
    missing = [w for w in wanted if w not in names]
    if missing:
        raise SystemExit(
            f"registryName check FAIL: 不在册 {missing}；在册示例 "
            f"{sorted(names)[:6]} …（幻觉源一律不许进蓝图，先登记再铸）")
    print(f"  registryName check: OK（{len(wanted)} 个均在册）")


def run_redline_scan(manifest: dict) -> None:
    """技能正文红线扫描（enable-constructor-skill-authoring W4）。

    扫描对象＝内容层文本（skills[].content/description、agents[].persona）；
    结构字段不扫。命中即抛 SystemExit 阻断铸造——错误由扫描器给出
    「文件: 字段「技能名」第 N 行 [规则] 片段 + 修法」。
    """
    # 实跑副本与扫描器同在 skills/scripts/；仓内源分居 tooling/ 与 scripts/，故两处都找。
    candidates = [
        Path(__file__).with_name("skill_redline_scan.py"),
        Path(__file__).resolve().parent.parent / "scripts" / "skill_redline_scan.py",
    ]
    scanner = next((p for p in candidates if p.exists()), None)
    if scanner is None:
        raise SystemExit(
            "redline scan FAIL: 扫描器缺失（找过 "
            + ", ".join(str(p) for p in candidates)
            + "）；红线门不可旁路静默（确要跳过请显式加 --skip-redline 并自负后果）")

    payload = {
        "skills": manifest.get("skills") or [],
        "agents": manifest.get("agents") or [],
    }
    out = subprocess.run(
        [sys.executable, "-I", str(scanner), "-"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode == 0:
        print("  skill redline scan: OK（无红线命中）")
        return
    detail = (out.stdout or "") + (out.stderr or "")
    raise SystemExit(f"skill redline scan FAIL:\n{detail.strip()}")


def run_prevalidation(fleet_dir: Path) -> None:
    paas = Path("/Users/chengsishi/finddata/paas")
    node = shutil.which("node")
    tpl_has_fleet = bool(json.loads((fleet_dir / "template.json").read_text(encoding="utf-8")).get("fleet"))
    if not tpl_has_fleet:
        # 岗位包（roles pack）没有 fleet 段——parseFleetTemplate 对它必然报
        # no fleet section，那不是缺陷，是形状。包级校验照跑。
        print("  parseFleetTemplate: SKIPPED（岗位包无 fleet 段）")
    elif paas.exists() and node:
        script = (
            "import { parseFleetTemplate } from './server/fleet-template.js';"
            "import { readFileSync } from 'node:fs';"
            f"const r = parseFleetTemplate(JSON.parse(readFileSync({json.dumps(str(fleet_dir / 'template.json'))}, 'utf8')));"
            "console.log(JSON.stringify(r));"
        )
        out = subprocess.run([node, "--input-type=module", "-e", script], cwd=str(paas),
                             capture_output=True, text=True, timeout=60)
        try:
            verdict = json.loads(out.stdout.strip() or "{}")
        except json.JSONDecodeError:
            verdict = {"ok": False, "error": out.stderr.strip()[:300]}
        if not verdict.get("ok"):
            raise SystemExit(f"parseFleetTemplate FAIL: {verdict.get('error')}")
        print("  parseFleetTemplate: OK")
    else:
        print("  parseFleetTemplate: SKIPPED (paas repo or node missing)")
    wrapper = Path.home() / ".agents/skills/facet-pack-builder/scripts/validate_manifest.py"
    if wrapper.exists():
        out = subprocess.run([sys.executable, str(wrapper), str(fleet_dir / "manifest.json")],
                             capture_output=True, text=True)
        lines = (out.stdout + out.stderr).strip().splitlines()
        line = lines[-1] if lines else ""
        if out.returncode != 0 or "✗" in line:
            raise SystemExit(f"validatePackManifest FAIL: {line}")
        print(f"  validatePackManifest: OK ({line})")


def main() -> None:
    global ledger
    ap = argparse.ArgumentParser()
    ap.add_argument("fleet_dir", type=Path)
    ap.add_argument("--check", action="store_true", help="不改盘：对比既有 manifest.json 是否与铸造结果一致")
    ap.add_argument("--repo-root", type=Path, default=None, help="fleet-packs 所在仓根（默认自动上溯）")
    ap.add_argument("--no-registry-check", action="store_true",
                    help="离线放行 registryName 在册校验（未取得在册表时默认硬拦）")
    ap.add_argument("--skip-redline", action="store_true",
                    help="跳过技能正文红线扫描（默认必跑；仅存量已发布包回铸时显式使用）")
    args = ap.parse_args()
    fleet_dir = args.fleet_dir.resolve()
    root = args.repo_root.resolve() if args.repo_root else _find_repo_root(fleet_dir)
    ledger = parse_ledger(root)

    manifest = build_manifest(fleet_dir)
    if not args.skip_redline:
        run_redline_scan(manifest)
    out_path = fleet_dir / "manifest.json"
    if args.check:
        existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
        if existing == manifest:
            print(f"CHECK OK: {out_path.name} 与铸造结果逐字段一致")
            return
        diff = [k for k in set(manifest) | set(existing or {})
                if (existing or {}).get(k) != manifest.get(k)]
        raise SystemExit(f"CHECK FAIL: drifted keys = {diff}")
    # 顺序纪律：**任何会失败的校验都不许留下副作用**。
    # 2026-11-13 实测 `cast … --skip-redline`：registryName 校验 fail-closed 退出 1，
    # 但文件已写下去——一次失败的铸造把「源产不出的内容」悄悄删掉（该包 manifest
    # fleet 2075B→1639B、i18n 1081B→1012B），得靠人从备份还原。
    # 现在：注册处校验前置（不需要文件）；prevalidation 需要落盘后的路径，故写完再跑，
    # 但失败时把原字节写回去，保证「铸造失败 = 什么都没发生」。
    check_registry_names(manifest["mcpServers"], allow_offline_miss=args.no_registry_check)
    prev = out_path.read_bytes() if out_path.exists() else None
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"cast -> {out_path}")
    try:
        run_prevalidation(fleet_dir)
    except BaseException:
        if prev is None:
            out_path.unlink(missing_ok=True)
        else:
            out_path.write_bytes(prev)
        print(f"cast ROLLED BACK -> {out_path}（原文件已还原）", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
