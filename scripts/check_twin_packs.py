#!/usr/bin/env python3
"""双胞胎判据（openspec: fleet-drift-and-hygiene / fleet-supply-drift-gates）。

同一业务场景的「模板包」与「岗位包」若声明同一组岗位 id，其 persona 与
`mcpServers[].allowTools` 必须逐字一致——除非其中一侧显式登记为派生。

为什么单独立一支而不并进 check_drift.py：那支的职责是「源 == 市场现行版」，
需要读市场（facet 代理通道 + token）。本判据**纯仓内**，干净 clone 可跑、
不需要网络、不需要凭据。混进那一支会让「离线可跑」这条既有纪律失效，也会
让两种失败（源漂移 / 双胞胎分叉）挤在同一个退出码里。

已抓到的真缺陷（2026-11-18 首跑）：`erp/erp-invoice-fleet` 与
`erp/erp-invoice-fleet-roles` 的巡检岗 persona 差一整段【待审队列】
（554 字 vs 417 字），而 roles 包才是实际装成岗位 preset 的那一个。
其余 6 对逐字相同。

用法：
    python3 fleet-packs/scripts/check_twin_packs.py              # 全仓
    python3 fleet-packs/scripts/check_twin_packs.py --detail     # 逐行打印分叉片段
    python3 fleet-packs/scripts/check_twin_packs.py --self-test  # 四条负向对照臂
退出码：0=全绿；1=有红；2=判不了（仓读不到 / 台账解析失败）。
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 派生登记：任一侧 `_casting.derivedFrom` 指向另一侧，即视为有意分叉，免检。
# 用既有的 `_casting` 家族而不是新造顶层键——那是本仓记录铸造来源的地方。
DERIVED_FIELD = "derivedFrom"

# 挂账（照 check_drift.py 的 KNOWN_LEAKS 惯例）：已确认的漂移、处置归别的线时，
# 登记在此而不是让闸常年红——常年红等于没人看。**每条必须带处置线 + 登记日**，
# 且每次运行都打印出来，不许静默通过。
#
# 2026-11-18 首跑时这里挂着 `erp-invoice-fleet ↔ erp-invoice-fleet-roles`：
# 模板包加了「待审开票申请」流程（巡检岗数、核对岗不越权、拍板岗 decide_event 过门），
# roles 包没跟上 ⇒ 活舰队实际跑的人设缺这三段纪律，工具面也少 query_events/decide_event。
# 当天已修（roles 包按模板包逐字同步三段 persona + 两件工具），挂账清空。
# 空表也是判据的一部分：它证明「挂账」不是常设豁免通道。
KNOWN_DRIFT: dict[str, str] = {}


def load_manifest(pack_dir: Path) -> dict | None:
    f = pack_dir / "manifest.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"⚠️  {pack_dir.name}: manifest.json 不是合法 JSON（{e}）", file=sys.stderr)
        return None


def find_pairs(root: Path) -> list[tuple[Path, Path]]:
    """找 `<x>-fleet` / `<x>-fleet-roles` 配对。两目录同层且有 manifest 才算。"""
    pairs = []
    for roles_dir in sorted(root.glob("*/*-fleet-roles")):
        if not roles_dir.is_dir():
            continue
        fleet_dir = roles_dir.with_name(roles_dir.name[: -len("-roles")])
        if fleet_dir.is_dir() and (fleet_dir / "manifest.json").exists() \
                and (roles_dir / "manifest.json").exists():
            pairs.append((fleet_dir, roles_dir))
    return pairs


def is_derived(a: dict, b: dict) -> bool:
    """任一侧登记了派生关系即免检。值可以是对方包名或任意说明串。"""
    for side in (a, b):
        raw = side.get("_casting") or {}
        val = raw.get(DERIVED_FIELD) if isinstance(raw, dict) else None
        if val:
            return True
    return False


def allowtools_of(manifest: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for srv in manifest.get("mcpServers") or []:
        if not isinstance(srv, dict):
            continue
        name = srv.get("registryName")
        if not name:
            continue
        tools = srv.get("allowTools") or []
        out[name] = sorted(tools) if isinstance(tools, list) else []
    return out


def compare_pair(fleet_dir: Path, roles_dir: Path, detail: bool = False) -> list[str]:
    """返回该对的判红行；空列表 = 判绿。"""
    a = load_manifest(fleet_dir)
    b = load_manifest(roles_dir)
    if a is None or b is None:
        return []  # 读不到已在 load_manifest 里告警；不算红（判不了）
    label = f"{fleet_dir.name} ↔ {roles_dir.name}"

    if is_derived(a, b):
        print(f"➖ {label}: 已登记派生（{DERIVED_FIELD}），免检")
        return []

    ag_a = {x.get("id"): x for x in (a.get("agents") or []) if isinstance(x, dict)}
    ag_b = {x.get("id"): x for x in (b.get("agents") or []) if isinstance(x, dict)}
    common = sorted(set(ag_a) & set(ag_b))
    if not common:
        print(f"➖ {label}: 无同名岗位，不构成双胞胎")
        return []

    problems: list[str] = []
    for aid in common:
        pa = ag_a[aid].get("persona") or ""
        pb = ag_b[aid].get("persona") or ""
        if pa != pb:
            problems.append(f"persona 不一致 [{aid}] {len(pa)} 字 vs {len(pb)} 字")
            if detail:
                diff = difflib.unified_diff(
                    pa.split("\n"), pb.split("\n"),
                    fromfile=f"{fleet_dir.name}/{aid}", tofile=f"{roles_dir.name}/{aid}",
                    lineterm="",
                )
                for line in list(diff)[:30]:
                    print(f"      {line[:160]}")
                print()

    ta = allowtools_of(a)
    tb = allowtools_of(b)
    for srv in sorted(set(ta) | set(tb)):
        if srv not in ta or srv not in tb:
            problems.append(f"allowTools 服务器不齐 [{srv}] 仅{'模板包' if srv in ta else '岗位包'}声明")
        elif ta[srv] != tb[srv]:
            problems.append(f"allowTools 不一致 [{srv}] {len(ta[srv])} 件 vs {len(tb[srv])} 件")

    if problems:
        print(f"❌ {label}")
        for p in problems:
            print(f"     · {p}")
    else:
        print(f"✅ {label}（{len(common)} 岗逐字一致）")
    return problems


def run(root: Path, detail: bool = False) -> int:
    pairs = find_pairs(root)
    if not pairs:
        print(f"❌ {root} 下没有找到任何 *-fleet / *-fleet-roles 配对——判不了", file=sys.stderr)
        return 2
    total_bad = 0
    pending: list[str] = []
    for fleet_dir, roles_dir in pairs:
        label = f"{fleet_dir.name} ↔ {roles_dir.name}"
        problems = compare_pair(fleet_dir, roles_dir, detail)
        if problems and label in KNOWN_DRIFT:
            print(f"   🔶 挂账（不计红）：{KNOWN_DRIFT[label]}")
            pending.append(label)
            continue
        total_bad += len(problems)
    print(f"\n合计 {len(pairs)} 对，{total_bad} 处漂移，{len(pending)} 对挂账")
    if pending:
        print("   挂账项每次都会打印，绝不静默通过；处置见 KNOWN_DRIFT 注释。")
    return 1 if total_bad else 0


# ── 负向对照：判据必须能红 ────────────────────────────────────────────────

def self_test() -> int:
    """每条臂都要证明「该红的红、该绿的绿」。没有对照的判据不是判据。"""
    import shutil

    def mkpack(root: Path, slug: str, agents: dict, mcp: dict, casting: dict | None = None) -> None:
        d = root / "erp" / slug
        d.mkdir(parents=True, exist_ok=True)
        doc = {
            "name": slug, "category": "fleet", "visibility": "public",
            "agents": [{"id": k, "name": k, "description": "", "persona": v} for k, v in agents.items()],
            "mcpServers": [{"registryName": s, "allowTools": t} for s, t in mcp.items()],
        }
        if casting:
            doc["_casting"] = casting
        (d / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    base = {"w": "同一段 persona", "d": "决策岗"}
    cases: list[tuple[str, bool, dict, dict, dict, dict, dict | None, dict | None]] = [
        # (name, expect_red, fleet_agents, roles_agents, fleet_mcp, roles_mcp, fleet_casting, roles_casting)
        ("A 逐字相同 → 绿", False, base, dict(base), {"s": ["t1"]}, {"s": ["t1"]}, None, None),
        ("B persona 分叉 → 红", True, base, {**base, "w": "另一段"}, {"s": ["t1"]}, {"s": ["t1"]}, None, None),
        ("C allowTools 广度不一致 → 红", True, base, dict(base), {"s": ["t1", "t2"]}, {"s": ["t1"]}, None, None),
        ("D 登记派生 → 绿", False, base, {**base, "w": "另一段"}, {"s": ["t1"]}, {"s": ["t2"]}, None, {"derivedFrom": "erp-x-fleet"}),
    ]

    passed = 0
    for name, expect_red, fa, ra, fm, rm, fc, rc in cases:
        tmp = Path(tempfile.mkdtemp(prefix="twinpair-"))
        try:
            mkpack(tmp, "erp-x-fleet", fa, fm, fc)
            mkpack(tmp, "erp-x-fleet-roles", ra, rm, rc)
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_code = run(tmp)
            got_red = rc_code == 1
            ok = got_red == expect_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（退出码 {rc_code}）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 第五臂：挂账不得等于静默通过——挂账时退出码 0，但输出必须带 🔶 与处置线。
    tmp = Path(tempfile.mkdtemp(prefix="twinpair-"))
    try:
        mkpack(tmp, "erp-x-fleet", base, {"s": ["t1"]})
        mkpack(tmp, "erp-x-fleet-roles", {**base, "w": "另一段"}, {"s": ["t1"]})
        import io, contextlib
        buf = io.StringIO()
        saved = dict(KNOWN_DRIFT)
        KNOWN_DRIFT["erp-x-fleet ↔ erp-x-fleet-roles"] = "self-test 挂账"
        try:
            with contextlib.redirect_stdout(buf):
                rc_code = run(tmp)
        finally:
            KNOWN_DRIFT.clear()
            KNOWN_DRIFT.update(saved)
        out = buf.getvalue()
        ok = rc_code == 0 and "🔶 挂账" in out and "self-test 挂账" in out and "挂账项每次都会打印" in out
        passed += ok
        print(f"  {'✅' if ok else '❌'} E 挂账不静默通过（退出码 {rc_code}，🔶 {'在' if '🔶' in out else '缺'}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(cases) + 1
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO), help="仓根（默认本仓）")
    ap.add_argument("--detail", action="store_true", help="逐行打印 persona 分叉片段")
    ap.add_argument("--self-test", action="store_true", help="四条负向对照臂")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 仓根不存在：{root}", file=sys.stderr)
        return 2
    return run(root, args.detail)


if __name__ == "__main__":
    sys.exit(main())
