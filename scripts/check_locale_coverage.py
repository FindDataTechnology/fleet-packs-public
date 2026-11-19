#!/usr/bin/env python3
"""在册包的四语覆盖判据（openspec: fleet-drift-and-hygiene / 发布阻塞可见化）。

发布门（`validate_manifest.py --require-locales en,ja,es,fr`）要求每个在册包
带 en/ja/es/fr 四块显示副轨。但仓里有 24 个包只写了 en，其中 **21 个已经在
市场上**——它们下一次改版会被门直接拒，而这件事今天没有任何地方看得见。

这不是「包写得不好」，是**一条真实的发布阻塞**：谁去改这 21 个包中的任何一个，
发版都会失败，而失败原因与他要改的内容毫无关系。判据的价值是把这条阻塞在
他动手之前就说清楚，而不是等他撞上。

判据：台账里登记的包（= 已上架/准备上架），若声明了 fleet 或 agents，
其 `i18n` 必须覆盖 en/ja/es/fr 四个键。

**为什么只判台账里的包**：不在台账里的（纯技能包、草稿、未上架的实验件）不受
发布门约束，给它们判红是造假的严格。`adopted/*` 的第三方技能包走另一条线
（`pack-locale-report.py` 有各自的处置）。

**为什么不是判红就完事**：补四语是逐包的翻译工作（24 个包 × 3 语 × 名称/描述/
岗位名/技能描述），不是一条脚本能代劳的。所以本判据输出**待补清单与工作量**，
退出码表达「有没有阻塞」，处置归各线。KNOWN_SHORT 里登记「已知且暂缓」的包，
每次运行打印 ---- 不静默通过。

用法：
    python3 scripts/check_locale_coverage.py                # 全仓
    python3 scripts/check_locale_coverage.py --self-test    # 五条负向对照臂
退出码：0=在册包四语齐；1=有缺（阻塞可见）；2=判不了（台账读不到）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROW_RE = re.compile(r"^\|\s*([a-z0-9][a-z0-9-]+)\s*\|\s*`([A-Za-z0-9_-]{8,})`")
REQUIRED = ("en", "es", "fr", "ja")

# 已知且暂缓：包名 → 理由。**每次运行都会打印**，不许静默通过。
# 空表意味着「没有暂缓项」，这是判据健康的表现，不是缺省。
KNOWN_SHORT: dict[str, str] = {}


def load_ledger_slugs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            out.add(m.group(1))
    return out


def index_packs(root: Path) -> dict[str, dict]:
    """目录名 → manifest。三个层级都收（adopted/anthropic-skills/x 是三层）。"""
    idx: dict[str, dict] = {}
    for depth in ("*/*", "*/*/*", "*/*/*/*"):
        for f in sorted(root.glob(f"{depth}/manifest.json")):
            if "/.git/" in str(f):
                continue
            try:
                idx[f.parent.name] = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return idx


def check(root: Path) -> tuple[int, int]:
    """返回 (缺语包数, 判不了哨兵)。"""
    slugs = load_ledger_slugs(root / "README.md")
    if not slugs:
        print(f"❌ 台账读不到或为空：{root / 'README.md'}——判不了", file=sys.stderr)
        return (-1, 0)

    idx = index_packs(root)
    short: list[tuple[str, list[str], list[str]]] = []  # (包目录名, 已有语, 缺的语)
    checked = 0
    for slug in sorted(slugs):
        manifest = idx.get(slug)
        if manifest is None:
            continue
        # 只判有「显示内容」的包：fleet 段或 agents[] 才有名称/描述要翻译
        if not (manifest.get("fleet") or manifest.get("agents")):
            continue
        checked += 1
        langs = sorted((manifest.get("i18n") or {}).keys())
        missing = [x for x in REQUIRED if x not in langs]
        if missing:
            short.append((slug, langs, missing))

    red = 0
    for slug, langs, missing in short:
        if slug in KNOWN_SHORT:
            print(f"➖ {slug}（暂缓：{KNOWN_SHORT[slug]}）缺 {','.join(missing)}")
            continue
        print(f"❌ {slug}: 有四语门但只写了 {','.join(langs) or '（无）'}，"
              f"缺 {','.join(missing)}——下次改版会被发布门直接拒")
        red += 1

    print(f"\n合计判了 {checked} 个在册包，{red} 个四语不齐"
          f"（{len(KNOWN_SHORT)} 个登记暂缓）")
    if red:
        print("处置：补 i18n.{es,fr,ja}（含 top-level name/description、"
              "逐 fleet.members 的 title/kpiSlots label、逐 agents 的 name、逐 skills 的 description）。")
    return (red, 0)


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    import contextlib
    import io
    import shutil

    def build(root: Path, packs: dict[str, dict], ledger: list[str]) -> None:
        for slug, m in packs.items():
            d = root / "cat" / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        lines = ["| pack | packId | 版本 | 说明 |", "|---|---|---|---|"]
        for i, slug in enumerate(ledger):
            lines.append(f"| {slug} | `{'P'*10}{i:02d}` | v1 | t |")
        (root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    FULL = {k: {"name": k} for k in REQUIRED}
    cases = [
        ("A 四语齐 → 绿", 0, {"pkg-a": {"agents": [{"id": "x"}], "i18n": FULL}}, ["pkg-a"]),
        ("B 只有 en → 红 1", 1, {"pkg-a": {"agents": [{"id": "x"}], "i18n": {"en": {}}}}, ["pkg-a"]),
        ("C 缺 ja → 红 1", 1, {"pkg-a": {"fleet": {"members": []}, "i18n": {k: {} for k in ("en", "es", "fr")}}}, ["pkg-a"]),
        # D 的台账里必须有一个**别的**包：台账整体为空时，判据只能报「判不了」，
        # 与「这个包不在台账」在语义上不可区分（自测抓到的 fixture 设计缺陷）。
        ("D 不在台账 → 不判（纯技能包/草稿）", 0,
         {"pkg-a": {"skills": [{"name": "s"}], "i18n": {"en": {}}},
          "pkg-other": {"agents": [{"id": "y"}], "i18n": FULL}}, ["pkg-other"]),
        ("E 无 fleet 无 agents → 不判", 0, {"pkg-a": {"skills": [{"name": "s"}]}}, ["pkg-a"]),
    ]
    passed = 0
    for name, want_red, packs, ledger in cases:
        root = Path(tempfile.mkdtemp(prefix="locale-"))
        try:
            build(root, packs, ledger)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(root)
            ok = red == want_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（红 {red}/{want_red}）")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # F：台账读不到 → 判不了
    root = Path(tempfile.mkdtemp(prefix="locale-"))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            red, _ = check(root)
        ok = red == -1
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 台账缺失 → 判不了（哨兵 {red}）")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    total = len(cases) + 1
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 仓根不存在：{root}", file=sys.stderr)
        return 2
    red, _ = check(root)
    if red == -1:
        return 2
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
