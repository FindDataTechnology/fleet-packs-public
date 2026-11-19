#!/usr/bin/env python3
"""跨仓副本判据（openspec: fleet-drift-and-hygiene / 定义收敛 §9）。

同一份 agent 定义在多个仓里各存一份，会各自演化——而且**会向不同的方向演化**，
各自都补了自己那侧的教训，谁也不知道对方补了什么。

2026-11-18 实测：`paas/.claude/agents/` 与 `fd-erp/.claude/agents/` 的四个
`impeccable-*` 定义**四份全部不同**。fd-erp 那侧更新更完整（有两笔明确提交：
「按本次长程实测补 impeccable 子代理的四个盲区」与「manual-edit-applier 的撤销
路径没写，补成『记住字节 + 禁 VCS 回滚』」），而 paas 那侧缺的正是这些：

  · `manual-edit-applier` 缺「撤销要记住文件字节，Never undo with git checkout --
    / git restore / git stash，因为文件可能带别人未提交的活」——这条正是
    `git-checkout-dot-wipes-uncommitted-work` 那个坑的守卫。
  · `finish-reviewer` 缺「截图对文档溢出结构性失明，要求每个宽度一行
    scrollWidth vs innerWidth 的实测」。
  · `documenter` 缺「已有 DESIGN.md 时先问用户 refresh/overwrite/merge」。
  · `asset-producer` 缺 `## Review handoff` 段。

所以本判据只做一件事：**同名定义在多仓出现时逐字节相同**，否则判红并指名哪一侧
多了什么。方向（谁是真源）不靠猜——`SOURCES` 声明真源仓，其余侧必须与它一致。

用法：
    python3 scripts/check_cross_repo_copies.py                # 用默认真源
    python3 scripts/check_cross_repo_copies.py --self-test    # 五条负向对照臂
    FP_WORKSPACE_ROOT=/path/to/finddata ...                   # 显式工作区根
退出码：0=全绿；1=有分叉；2=判不了（工作区根找不到 / 真源缺失）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 受管的跨仓副本族：真源仓 → 必须与真源逐字节一致的镜像仓。
# 加一项就自动被守；不要靠「记得同步」。
FAMILIES: list[dict] = [
    {
        "glob": ".claude/agents/impeccable-*.md",
        "source": "fd-erp",
        "mirrors": ["paas"],
        # 为什么真源是 fd-erp 而不是 paas：见模块 docstring 的实测（四份全部不同，
        # fd-erp 侧是更完整的那个，且它有对应的提交记录可追溯）。
    },
]


def workspace_roots() -> list[Path]:
    """工作区根候选链（照 check_drift.py 的同名函数，理由见那里的注释）。"""
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


def resolve_ws() -> Path | None:
    for p in workspace_roots():
        if p.is_dir() and (p / "fleet-packs").is_dir():
            return p
    return None


def compare(ws: Path, fam: dict) -> tuple[int, int]:
    """返回 (红数, 判不了)。"""
    src_repo = ws / fam["source"]
    if not src_repo.is_dir():
        print(f"➖ 真源仓不存在：{fam['source']}——未判", file=sys.stderr)
        return (0, 1)
    sources = {p.name: p for p in sorted(src_repo.glob(fam["glob"]))}
    if not sources:
        print(f"❌ 真源 `{fam['source']}` 下没有 {fam['glob']} 的匹配——判不了", file=sys.stderr)
        return (-1, 0)

    red = 0
    for mirror in fam["mirrors"]:
        m_repo = ws / mirror
        if not m_repo.is_dir():
            print(f"➖ 镜像仓不存在：{mirror}——未判", file=sys.stderr)
            continue
        mirrors = {p.name: p for p in sorted(m_repo.glob(fam["glob"]))}
        # 镜像侧多出的文件也算分叉（有人在一侧新建了一份）
        for name in sorted(set(mirrors) - set(sources)):
            print(f"❌ {mirror}/{name} 在真源 `{fam['source']}` 里不存在（单边新增）")
            red += 1
        for name, sp in sorted(sources.items()):
            mp = mirrors.get(name)
            if mp is None:
                print(f"❌ {mirror}/{name} 缺失（真源有，镜像无）")
                red += 1
                continue
            sb, mb = sp.read_bytes(), mp.read_bytes()
            if sb == mb:
                print(f"✅ {name}（{mirror} = {fam['source']}）")
            else:
                print(f"❌ {name} 两侧不同：{fam['source']} {len(sb)}B vs {mirror} {len(mb)}B")
                red += 1
    return (red, 0)


def run(ws: Path) -> int:
    total_red = 0
    unjudged = 0
    for fam in FAMILIES:
        red, uj = compare(ws, fam)
        if red == -1:
            return 2
        total_red += red
        unjudged += uj
    print(f"\n合计 {total_red} 处分叉，{unjudged} 项未判")
    return 1 if total_red else 0


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    import contextlib
    import io
    import shutil

    def mk(ws: Path, repo: str, files: dict[str, str]) -> None:
        d = ws / repo / ".claude" / "agents"
        d.mkdir(parents=True, exist_ok=True)
        (ws / repo / ".git").mkdir(exist_ok=True)
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")

    base = {"impeccable-a.md": "same\n", "impeccable-b.md": "same\n"}
    cases = [
        ("A 全一致 → 绿", 0, base, dict(base), None),
        ("B 内容不同 → 红 1", 1, base, {**base, "impeccable-a.md": "drift\n"}, None),
        ("C 镜像缺失一份 → 红 1", 1, base, {"impeccable-a.md": "same\n"}, None),
        ("D 镜像多出一份 → 红 1", 1, base, {**base, "impeccable-c.md": "extra\n"}, None),
        ("E 真源仓不存在 → 未判不计红", 0, base, dict(base), "drop-source"),
    ]
    passed = 0
    for name, want_red, src_files, mir_files, special in cases:
        ws = Path(tempfile.mkdtemp(prefix="xrepo-"))
        try:
            (ws / "fleet-packs").mkdir()
            if special != "drop-source":
                mk(ws, "fd-erp", src_files)
            mk(ws, "paas", mir_files)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = run(ws)
            got_red = rc == 1
            ok = got_red == (want_red == 1)
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（退出码 {rc}）")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    # 第六臂：真源目录在、但一个文件都没有 → 判不了（不是绿）
    ws = Path(tempfile.mkdtemp(prefix="xrepo-"))
    try:
        (ws / "fleet-packs").mkdir()
        mk(ws, "fd-erp", {})
        mk(ws, "paas", {})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = run(ws)
        ok = rc == 2
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 真源无匹配 → 判不了（退出码 {rc}）")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    total = len(cases) + 1
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    ws = resolve_ws()
    if ws is None:
        print("❌ 找不到 finddata 工作区根（给 FP_WORKSPACE_ROOT）——判不了", file=sys.stderr)
        return 2
    return run(ws)


if __name__ == "__main__":
    sys.exit(main())
