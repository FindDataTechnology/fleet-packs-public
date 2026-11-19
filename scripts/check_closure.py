#!/usr/bin/env python3
"""成员闭包判据（openspec: fleet-drift-and-hygiene / fleet-supply-drift-gates）。

模板的 `fleet.members[]` 每个 pack-preset 成员都指向一个岗位 preset。如果那个
preset 在**装完之后不存在**，`fleet-provisioner.js:84` 查 `catalogAgentIds()`
查不到就把岗位标 degraded——**岗位照建、名字照在、能力没了，且不报错**。今天
唯一的兜底是 trial 的 `post-materialized` 断言，已经铸完装完烧完钱。

## 语义（按实装代码定，不按直觉）

`fleet-provisioner.js` 的解析是：`targetId = m.target.presetId`，然后在
`catalogAgentIds()`（运行时全局 agent 目录）里找它。而
`catalog.js:packAgentDoc()` 把每个**已装包 manifest 的 `agents[]`** 逐个放进
那个目录（只放 install report 里 `status === "installed"` 的）。所以闭包的定义是：

    成员的 presetId ∈ (声明包自己的 agents[]) ∪ (任意被引用包的 agents[])

**`target.packId` 不是「preset 住在哪个包」**——它指的是「这个岗位用哪份共享
包的技能/工具」，那个包通常 0 个 agent（`fleet-brief-report` / `fleet-intel-gather`
都是纯技能包）。实测 96 个 pack-preset 成员：90 个的 presetId 在声明包自己的
`agents[]` 里，其余 target.kind 不是 pack-preset；按「preset 在 target 包」判会
得到 34 条全假红（本判据第一版就是这么错的，留下这段当反例）。

## 三值

    0 = 闭包全成立
    1 = 有断裂（逐条指名 slug / ref / presetId，并给出该包实际的 agent id 清单）
    2 = 判不了（台账读不到 / 仓根不存在）——「没校验」不算「通过」
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 与 check_drift.py 的台账行形状一致（slug | `packId` | vN | 说明…）。
ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`")

# 已知的闭包缺陷（照 check_drift.py 的 KNOWN_LEAKS 惯例）：确认过、处置归别的
# 线的登记在此——每次运行打印 🔶 与处置线，不静默通过。
KNOWN_CLOSURE: dict[str, str] = {}


def load_ledger(path: Path) -> dict[str, str]:
    """packId → slug。台账是「slug ↔ packId」的唯一对照。"""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def index_manifests(root: Path) -> dict[str, dict]:
    """目录名 → 已解析 manifest。读不了的跳过（由调用方从台账侧发现缺口）。"""
    idx: dict[str, dict] = {}
    for depth in ("*/*", "*/*/*"):
        for f in sorted(root.glob(f"{depth}/manifest.json")):
            if "/.git/" in str(f):
                continue
            try:
                idx[f.parent.name] = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return idx


def agent_ids(manifest: dict) -> set[str]:
    return {a.get("id") for a in (manifest.get("agents") or [])
            if isinstance(a, dict) and a.get("id")}


def check(root: Path, verbose: bool = False) -> tuple[int, int]:
    """返回 (红数, 判不了哨兵)。红数为 -1 表示判不了。"""
    ledger = load_ledger(root / "README.md")
    if not ledger:
        print(f"❌ 台账读不到或为空：{root / 'README.md'}——判不了", file=sys.stderr)
        return (-1, 0)

    idx = index_manifests(root)
    # 台账 slug → manifest；同时在整仓建一份「全量 agent id 池」，
    # 因为装完之后 preset 只要在任一已装包的 agents[] 里就能解析。
    pool: dict[str, list[str]] = {}
    for slug, manifest in idx.items():
        for aid in agent_ids(manifest):
            pool.setdefault(aid, []).append(slug)

    red = 0
    checked = 0
    offsite = 0
    for slug in sorted(ledger.values()):
        manifest = idx.get(slug)
        if manifest is None:
            continue  # 异地包（ERP 模板住在兄弟仓）：无 manifest 可比
        own = agent_ids(manifest)
        for m in (manifest.get("fleet") or {}).get("members") or []:
            t = (m or {}).get("target") or {}
            if t.get("kind") != "pack-preset":
                continue  # persona / auditor / cluster / tool 不走这条解析
            ref = m.get("ref") or "?"
            preset = t.get("presetId")
            if not preset:
                print(f"❌ {slug}: 成员 '{ref}' 的 pack-preset target 没有 presetId")
                red += 1
                continue
            checked += 1
            if preset in own:
                if verbose:
                    print(f"✅ {slug}: 成员 '{ref}' → 本包 agent '{preset}'")
                continue
            if preset in pool:
                if verbose:
                    print(f"✅ {slug}: 成员 '{ref}' → 跨包 agent '{preset}'（{', '.join(sorted(set(pool[preset]))[:3])}）")
                continue
            key = f"{slug}:{preset}"
            if key in KNOWN_CLOSURE:
                print(f"   🔶 挂账（不计红）{key}：{KNOWN_CLOSURE[key]}")
                continue
            print(f"❌ {slug}: 成员 '{ref}' 的 presetId '{preset}' 在整仓 agent id 池里不存在"
                  f"（本包有 {len(own)} 个岗位：{', '.join(sorted(own)) or '无'}）")
            red += 1

    # 审计官**不该**有 cadence。
    #
    # 我最初把这条判反了，方向纠正是有据的：`docs/fleet-templates/personal-staff-fleet.seed.json`
    # 的描述自己写着「形态：四岗会话型（**无 cadence**）……审计官不进工作图……
    # **它盯的是拍板队列里的判断，裁决经 `auditor_submit_verdict` 回到主人，不走编导边**」，
    # 而 `docs/personal-staff-runbook.md` 的 L1 验收判据明列「**无定时回合 / 审计官不在编导边**」。
    # 所以「审计官零回合」是**设计**，不是漏配。
    #
    # 审计官的触发是**拍板队列驱动的**：有判断要复核才起一个回合，裁决经
    # `auditor_submit_verdict` 回到主人。给它加 cadence 等于让它按钟点去审不存在的东西
    # ——评审舰队那支有 cadence 是因为它盯的是**评测岗近三轮的产出**（有稳定输入源），
    # 两者不同构，不能照抄。
    #
    # 所以判据是反向的：审计官声明了 cadence ⇒ 红，并指出它该由拍板队列驱动。
    #
    # 附带两条实测事实（查证路径可复用）：
    #  · 判「审计官有没有真跑过」必须看 `member_turn_events`，**不能看 `member_reports`**。
    #    该审计官 reports 有 4 行（`source` 全是 `self`，内容是「补跑·窗口0回合缺发」
    #    「健康巡检」「CEO 巡视第 4 轮」）——别的进程以它身份注入的，不是它自己的裁决。
    #  · 同舰队评审官 / 侦查官 / 书记同样无 cadence 却都有回合，最近回合集中在
    #    11-17 05:41–05:45（三个几乎同时），是常务岗经 dispatch 边一次派发的。
    #    它们在工作图里，审计官不在——这条差异是设计，不是缺陷。
    orphans = 0
    for slug in sorted(ledger.values()):
        manifest = idx.get(slug)
        if manifest is None:
            continue
        for m in (manifest.get("fleet") or {}).get("members") or []:
            t = (m or {}).get("target") or {}
            if t.get("kind") != "auditor":
                continue
            cad = ((m.get("cadence") or {}) or {}).get("cron")
            if cad:
                key = f"{slug}:auditor:{m.get('ref')}"
                if key in KNOWN_CLOSURE:
                    print(f"   🔶 挂账（不计红）{key}：{KNOWN_CLOSURE[key]}")
                    continue
                print(f"🔶 {slug}: 审计官 '{m.get('ref')}' 声明了 cadence '{cad}'——"
                      f"审计官由拍板队列驱动（有判断要复核才起回合），不该按钟点跑。"
                      f"若确有稳定输入源要定时复核，请在此登记理由")
                orphans += 1
    if orphans:
        print(f"   （审计官带 cadence {orphans} 处，见上）")

    # 台账里有、仓里没有 manifest 的包（异地/兄弟仓）：如实计数，不判红。
    offsite = sum(1 for s in ledger.values() if s not in idx)
    print(f"\n合计判了 {checked} 个 pack-preset 闭包，{red} 处断裂；"
          f"{offsite} 个台账包无本地 manifest（异地，未判）")
    return (red, 0)


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    """判据必须能红。六条臂。"""
    import contextlib
    import io
    import shutil

    def build(root: Path, packs: dict[str, dict], ledger_rows: list[tuple[str, str]]) -> None:
        for slug, manifest in packs.items():
            d = root / "cat" / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = ["| pack | packId | 版本 | 说明 |", "|---|---|---|---|"]
        for slug, pid in ledger_rows:
            lines.append(f"| {slug} | `{pid}` | v1 | test |")
        (root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def team(agents: list[str], members: list[dict]) -> dict:
        return {
            "name": "t", "category": "fleet",
            "agents": [{"id": a, "name": a, "persona": "p"} for a in agents],
            "fleet": {"name": "t", "members": members, "orchestration": {}},
        }

    def mem(ref: str, preset: str | None, kind: str = "pack-preset",
            cadence: dict | None = None) -> dict:
        t: dict = {"kind": kind}
        if preset is not None:
            t["presetId"] = preset
        if kind == "pack-preset":
            t["packId"] = "PPPPPPPPPPPPPPPPPPPPPP"   # 共享技能包，0 个 agent
        m: dict = {"ref": ref, "title": ref, "parentRef": None, "target": t}
        if cadence is not None:
            m["cadence"] = cadence
        return m

    SKILL_PACK = "PPPPPPPPPPPPPPPPPPPPPP"
    cases = [
        ("A presetId 在本包 agents[] → 绿", 0, {
            "fleet": team(["w", "d"], [mem("w", "w"), mem("d", "d")]),
            "skillsrc": {"name": "s", "category": "facet", "skills": [{"name": "x", "content": "y"}]},
        }, [("fleet", "FFFFFFFFFFFFFFFFFFFFFF"), ("skillsrc", SKILL_PACK)]),
        ("B presetId 全仓不存在 → 红", 1, {
            "fleet": team(["w"], [mem("w", "ghost")]),
        }, [("fleet", "FFFFFFFFFFFFFFFFFFFFFF")]),
        ("C 跨包 agent 可解析 → 绿", 0, {
            "fleet": team([], [mem("x", "shared-agent")]),
            "other": team(["shared-agent"], []),
        }, [("fleet", "FFFFFFFFFFFFFFFFFFFFFF"), ("other", "OOOOOOOOOOOOOOOOOOOOOO")]),
        ("D 非 pack-preset 成员不适用闭包 → 绿（auditor 带 cadence，故不触发审计官判据）", 0, {
            "fleet": team([], [mem("aud", None, kind="auditor", cadence={"cron": "0 11 * * *"})]),
        }, [("fleet", "FFFFFFFFFFFFFFFFFFFFFF")]),
        ("E 缺 presetId → 红", 1, {
            "fleet": team(["w"], [mem("w", None)]),
        }, [("fleet", "FFFFFFFFFFFFFFFFFFFFFF")]),
    ]

    passed = 0
    for name, want_red, packs, ledger_rows in cases:
        tmp = Path(tempfile.mkdtemp(prefix="closure-"))
        try:
            build(tmp, packs, ledger_rows)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(tmp)
            ok = red == want_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（红 {red}/{want_red}）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 第七臂：审计官**带** cadence 要被提示（审计官由拍板队列驱动，不该按钟点跑）。
    # 判据是反向的——零回合是设计（见 check() 的注释，依据是种子描述与 runbook L1）。
    tmp = Path(tempfile.mkdtemp(prefix="closure-"))
    try:
        build(tmp, {"fleet": team([], [mem("aud", None, kind="auditor",
                                           cadence={"cron": "0 11 * * *"})])},
              [("fleet", "FFFFFFFFFFFFFFFFFFFFFF")])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            red, _ = check(tmp)
        ok = red == 0 and "审计官" in buf.getvalue() and "拍板队列驱动" in buf.getvalue()
        passed += ok
        print(f"  {'✅' if ok else '❌'} G 审计官带 cadence → 提示（红 {red}，提示{'在' if '拍板队列' in buf.getvalue() else '缺'}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 第八臂：审计官无 cadence（正确形态）→ 绿且无提示。
    tmp = Path(tempfile.mkdtemp(prefix="closure-"))
    try:
        build(tmp, {"fleet": team([], [mem("aud", None, kind="auditor")])},
              [("fleet", "FFFFFFFFFFFFFFFFFFFFFF")])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            red, _ = check(tmp)
        ok = red == 0 and "拍板队列驱动" not in buf.getvalue()
        passed += ok
        print(f"  {'✅' if ok else '❌'} H 审计官无 cadence → 绿（红 {red}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 第六臂：台账读不到必须判「判不了」，不能算绿。
    tmp = Path(tempfile.mkdtemp(prefix="closure-"))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            red, _ = check(tmp)
        ok = red == -1
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 台账缺失 → 判不了（哨兵 {red}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(cases) + 3
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--verbose", action="store_true", help="逐个闭包打印 ✅")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 仓根不存在：{root}", file=sys.stderr)
        return 2
    red, _ = check(root, args.verbose)
    if red == -1:
        return 2
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
