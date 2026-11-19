#!/usr/bin/env python3
"""工具名体检：包里声明的每一件工具，必须在**该 registry 的源码里**真存在（只读）。

抓的是「面上写了、手里没有」的最后一层：平台的 spine 投影只覆盖它认识的 registry，
不认识的（本仓实测四个：`fd-memory` / `fleet` / `fleet-architect` / `actions-mcp`）
真校验器只打一句「allowTools 未校验」就放过——2026-11-14 给「门店巡检三岗」验工具面时，
那句「未校验」指的是 9 件 fd-memory 工具 + 2 件 fleet 工具全部无人核对过。

为什么不能靠 spine 投影补齐：这四个 registry 的工具定义在**别的仓的源码里**
（`fd-memory/src/mcp.ts`、`paas/server/fleet-mcp.js` / `fleet-architect-mcp.js` /
`actions-mcp.js`），投影不认识它们不是缺陷，是覆盖面问题。

判据（只做断言，不做推断）：
  · 包里 `mcpServers[].allowTools` 与 `agents[].resources.mcpTools` 的每个名字，
    必须能在对应源码里找到 `name: "<tool>"` 定义；
  · 名字写错（`report_statuses`）、写点号串（`fleet.report_status`）、指向不存在的
    服务（源码里没那个 registry）都算命中；
  · registry 没进映射表 ⇒ 逐条打印「未映射，未核」并计入判不出，**不当通过**；
  · 一个 registry 的源码都读不到 ⇒ 退 2（判不出），不许把「没核」读成「都对」。

用法：
    python3 tooling/check_tool_names.py [--root <工作区根>] [paths...]
    python3 tooling/check_tool_names.py --root <工作区根> --strict-unverified
    python3 tooling/check_tool_names.py --self-test [--root <工作区根>]
退出码：0=已核的都在册（**有未核文件时另打 ⚠ 与 `DEGRADED:` 尾行**，那仍是 0）；
        1=有不在册的名字（或加了 `--strict-unverified` 且有文件未核）；2=判不出（源码取不到 / 一份都核不了）。
「未核」不等于「都对」：2026-11-17 实测 46 份人口里 25 份从没进过闸，而终判仍打「✅ 全部在册」。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# registry → (仓, 相对路径)。只收「本工作区里真有源码」的；新加一条要先读源码确认形状。
SOURCES = {
    "fd-memory": ("fd-memory", "src/mcp.ts"),
    "fleet": ("paas", "server/fleet-mcp.js"),
    "fleet-architect": ("paas", "server/fleet-architect-mcp.js"),
    "actions-mcp": ("paas", "server/actions-mcp.js"),
}

_TOOL_RE = re.compile(r'name:\s*"([a-z][a-z0-9_]+)"\s*,\s*\n\s*description:')


# 工作区根的判据是三样**同时**在场：`paas/`、`fd-memory/`、以及底座标记之一
# （`fd-infra-deploy/` 或 `openspec/`）。为什么不能只认「有 paas/ 和 fd-memory/」：
# 2026-11-14 实测把本仓 clone 到 `/tmp/fp-verify7` 时，`/tmp` 下恰好躺着别的会话留下的
# `paas/` 与 `fd-memory/` ⇒ 闸把**别人的工作副本**当成真源，干净克隆报绿。
# 「扫错树还绿」比报红坏得多：它给的是一个没人验证过的绿。
# **自动解析**要三样同时在场：`paas/`、`fd-memory/`、`fd-infra-deploy/`。
# 为什么第三样不能省（也不能用 `openspec/` 代）：2026-11-14 把本仓 clone 到 `/tmp/fp-verify7`
# 试干净克隆，`/tmp` 下恰好躺着别的会话留下的 `paas/`、`fd-memory/`、`openspec/` ⇒ 闸把
# **别人的工作副本**当成真源，干净克隆报绿。实测只有 `fd-infra-deploy/` 能把它分开。
# 「扫错树还绿」比报红坏得多：它给的是一个没人验证过的绿。
AUTO_MARKERS = ("paas", "fd-memory", "fd-infra-deploy")


def _auto_ok(p: Path) -> bool:
    return p.is_dir() and all((p / d).is_dir() for d in AUTO_MARKERS)


def _explicit_ok(p: Path) -> bool:
    """显式 --root：人已经指了树，只要求它确实是工作区且四份源码都在。"""
    if not (p.is_dir() and (p / "paas").is_dir() and (p / "fd-memory").is_dir()):
        return False
    return all((p / repo / rel).is_file() for repo, rel in SOURCES.values())


def workspace_root(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).resolve()
        return p if _explicit_ok(p) else None
    for cand in (REPO.parent, REPO.parent / "finddata"):
        if _auto_ok(cand):
            return cand
    for anc in REPO.parents:
        if _auto_ok(anc):
            return anc
    return None


def tools_of(root: Path, reg: str) -> set[str] | None:
    """读源码取出工具名；源码不在就返回 None（＝判不出，不是空集）。"""
    spec = SOURCES.get(reg)
    if spec is None:
        return None
    path = root / spec[0] / spec[1]
    if not path.is_file():
        return None
    return set(_TOOL_RE.findall(path.read_text(encoding="utf-8", errors="replace")))


def declared(manifest: dict) -> list[tuple[str, str, str]]:
    """(registry, tool, 出处) —— 包级 allowTools 与逐岗 mcpTools 一起收。"""
    out: list[tuple[str, str, str]] = []
    for s in manifest.get("mcpServers") or []:
        if not isinstance(s, dict):
            continue
        reg = s.get("registryName")
        for t in s.get("allowTools") or []:
            out.append((str(reg), str(t), f"mcpServers[{reg}].allowTools"))
    for a in manifest.get("agents") or []:
        if not isinstance(a, dict):
            continue
        mt = ((a.get("resources") or {}).get("mcpTools")
              if isinstance(a.get("resources"), dict) else None)
        if not isinstance(mt, dict):
            continue
        for reg, lst in mt.items():
            for t in lst or []:
                out.append((str(reg), str(t), f"agents[{a.get('id')}].resources.mcpTools['{reg}']"))
    return out


def check(manifest: dict, root: Path, cache: dict) -> tuple[list[dict], list[str], int]:
    """返回 (不在册, 未映射或未核, 已核名字数)。"""
    bad: list[dict] = []
    skipped: list[str] = []
    checked = 0
    for reg, tool, entry in declared(manifest):
        if reg not in cache:
            cache[reg] = tools_of(root, reg)
        have = cache[reg]
        if have is None:
            skipped.append(f"{entry}: registry `{reg}` 未映射或源码取不到——本名未核")
            continue
        checked += 1
        if tool not in have:
            bad.append({"registry": reg, "tool": tool, "entry": entry,
                        "error": f"`{reg}` 的源码里没有工具 `{tool}`"
                                 f"（该 registry 实有 {len(have)} 件：{sorted(have)}）"})
    return bad, skipped, checked


def self_test(root_arg=None) -> int:
    # `--root` 也喂给自证：否则自证只在本仓恰好位于工作区子目录时能跑，
    # 在独立 worktree（如 `/tmp/wt-*`）里一律「自检无处可跑」——那是布局问题，
    # 不是判据问题，却会让「自证绿」这件事在换树之后静默消失。
    root = workspace_root(root_arg)
    if root is None:
        print(f"✗ 判不出：找不到工作区根（要同时含 {'、'.join(d + '/' for d in AUTO_MARKERS)}）"
              f"——自检无处可跑", file=sys.stderr)
        return 2
    have = tools_of(root, "fleet")
    if not have:
        print("✗ 判不出：读不到 paas/server/fleet-mcp.js——自检无处可跑", file=sys.stderr)
        return 2
    real = sorted(have)[0]
    arms = [
        ("真名必须在册", {"mcpServers": [{"registryName": "fleet", "allowTools": [real]}]}, 0),
        ("拼错的名字必须红", {"mcpServers": [{"registryName": "fleet", "allowTools": [real + "s"]}]}, 1),
        ("逐岗点号串必须红",
         {"agents": [{"id": "p", "resources": {"mcpTools": {"fleet": [f"fleet.{real}"]}}}]}, 1),
        ("未映射的 registry 必须算判不出（不核、不报绿）",
         {"mcpServers": [{"registryName": "not-a-registry", "allowTools": [real]}]}, 0),
    ]
    passed = 0
    for label, man, want_bad in arms:
        bad, skipped, checked = check(man, root, {})
        got_ok = (len(bad) == 0) if want_bad == 0 else (len(bad) > 0)
        if want_bad == 0 and label.startswith("未映射"):
            got_ok = got_ok and checked == 0 and bool(skipped)
        print(f"  {'PASS' if got_ok else 'FAIL'}  {label}"
              f"（不在册 {len(bad)} / 未核 {len(skipped)} / 已核 {checked}）")
        passed += 1 if got_ok else 0
    # 反向臂（真历）：2026-11-14 把本仓 clone 到 /tmp/fp-verify7，/tmp 下躺着别的会话的
    # paas/ + fd-memory/ + openspec/ ⇒ 自动解析把**别人的工作副本**当真源、干净克隆报绿。
    # 两条各证一次：自动解析要 `fd-infra-deploy/`（分隔标记）；显式 --root 要四份源码都在。
    import tempfile
    with tempfile.TemporaryDirectory(prefix="fd-toolnames-") as td:
        look = Path(td)
        for d in ("paas", "fd-memory", "openspec"):
            (look / d).mkdir()
        auto_ok = not _auto_ok(look)
        explicit_ok = not _explicit_ok(look)
    print(f"  {'PASS' if auto_ok else 'FAIL'}  只有 paas/fd-memory/openspec 的目录不得被自动当成工作区根"
          f" — {'拒绝' if auto_ok else '扫错树还报绿（/tmp 就是这么假绿的）'}")
    passed += 1 if auto_ok else 0
    print(f"  {'PASS' if explicit_ok else 'FAIL'}  显式 --root 缺源码文件时也要判不出"
          f" — {'拒绝' if explicit_ok else '把「目录名对」当成了「真源在」'}")
    passed += 1 if explicit_ok else 0
    arms = arms + [("root", None, ""), ("root2", None, "")]

    # 终判四态：未核不得读成通过（2026-11-17 实测 25/46 份未核却打「✅ 全部在册」）。
    def _verdict_ok(rc, lines, want_rc, forbid_ok, want_deg):
        blob = "\n".join(lines)
        return (rc == want_rc
                and (("✅" not in blob) if forbid_ok else True)
                and (("DEGRADED:" in blob) == want_deg))

    v = [
        ("未核>0 ⇒ 不给 ✅、打 DEGRADED、rc=0（缺省降级不失败）",
         summary_verdict(0, 65, 25, False), 0, True, True),
        ("未核>0 + --strict-unverified ⇒ rc=1（这一档可升级为失败）",
         summary_verdict(0, 65, 25, True), 1, True, True),
        ("未核=0 ⇒ ✅ 且 rc=0（别把正常路径也降级）",
         summary_verdict(0, 65, 0, False), 0, False, False),
        ("有不在册的 ⇒ rc=1 优先于未核（红不该被降级盖住）",
         summary_verdict(3, 65, 25, False), 1, True, False),
    ]
    for label, (rc, lines), want_rc, forbid_ok, want_deg in v:
        ok = _verdict_ok(rc, lines, want_rc, forbid_ok, want_deg)
        print(f"  {'PASS' if ok else 'FAIL'}  {label} — rc={rc} lines={len(lines)}")
        passed += 1 if ok else 0
    arms = arms + [(label, None, "") for label, _v, _r, _f, _d in v]

    if len(arms) != SELF_TEST_ARMS:
        print(f"  ✗ 自证臂数 {len(arms)} ≠ SELF_TEST_ARMS {SELF_TEST_ARMS}（加/删臂后要同步那个常量）")
        return 1
    print(f"self-test {'绿' if passed == len(arms) else '红'} ({passed}/{len(arms)})")
    return 0 if passed == len(arms) else 1


SELF_TEST_ARMS = 10


def summary_verdict(total_bad: int, total_checked: int, unverifiable: int,
                    strict: bool) -> tuple[int, list[str]]:
    """终判（纯函数，自证用）：**未核不得读成通过**。

    2026-11-17 实测同一份输出里既写着「未核文件 25 份」又写着「✅ 全部在册」——
    25/46 份从未进过这道闸，而裸 ✅ 会把**覆盖面的缺口**读成「全仓都对」。
    这与 `validate_manifest.py` 的 `degraded=true` 是同一条纪律：形状/局部通过
    ≠ 判据跑过；要把它当失败由显式开关（`--strict-unverified`）表达。
    """
    if total_bad:
        return 1, []
    if total_checked == 0:
        return 2, ["✗ 判不出：一个名字都没核到（映射表的源码都取不到？）"]
    if unverifiable:
        return (1 if strict else 0), [
            f"⚠ 已核的都在册，但有 {unverifiable} 份**未核** —— 不得据此判「全仓在册」",
            "DEGRADED: 未核文件的 registry 不在映射表里"
            "（SOURCES 只收本工作区有源码的四个）"
            + ("" if strict else "（--strict-unverified 可把这一档升级为失败）"),
        ]
    return 0, ["✅ 全部在册"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="工具名体检：声明的工具必须在 registry 源码里真存在")
    ap.add_argument("paths", nargs="*", help="manifest.json 路径（缺省＝仓内全部）")
    ap.add_argument("--root", help="工作区根（含 paas/ 与 fd-memory/）")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict-unverified", action="store_true",
                    help="把「有文件未核」这一档升级为失败（缺省 rc=0 但打 DEGRADED）")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test(a.root)

    root = workspace_root(a.root)
    if root is None:
        where = a.root or "、".join(str(c) for c in (REPO.parent, REPO.parent / "finddata"))
        print(f"✗ 判不出：找不到工作区根（试过 {where}；自动解析要求同时含 "
              f"{'、'.join(d + '/' for d in AUTO_MARKERS)}；显式 --root 还要求四份源码都在）"
              f"——用 --root 指向真工作区，别让闸去读旁边某棵树里的同名目录", file=sys.stderr)
        return 2
    print(f"真源根：{root}（{'显式' if a.root else '自动解析'}）")
    paths = [Path(p) for p in a.paths] or sorted(set(REPO.glob("**/manifest.json")))
    cache: dict = {}
    total_bad = total_checked = 0
    unverifiable_files = 0
    for p in paths:
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"✗ 读不动 {p}: {type(e).__name__}", file=sys.stderr)
            return 2
        bad, skipped, checked = check(man, root, cache)
        total_bad += len(bad)
        total_checked += checked
        rel = p.relative_to(REPO) if p.is_relative_to(REPO) else p
        if bad:
            print(f"❌ {rel}  {len(bad)} 处不在册")
            for b in bad:
                print(f"   [{b['entry']}] {b['error']}")
        elif checked == 0 and skipped:
            unverifiable_files += 1
            print(f"ℹ️  {rel}  未核（{len(skipped)} 条引用的 registry 不在映射表里）")
    print(f"\n已核工具名 {total_checked} 个，不在册 {total_bad} 个"
          f"{f'，未核文件 {unverifiable_files} 份' if unverifiable_files else ''}")
    rc, lines = summary_verdict(total_bad, total_checked, unverifiable_files,
                                a.strict_unverified)
    for ln in lines:
        print(ln, file=sys.stderr if ln.startswith("✗") else sys.stdout)
    return rc


if __name__ == "__main__":
    sys.exit(main())
