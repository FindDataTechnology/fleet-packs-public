#!/usr/bin/env python3
"""发布面泄漏扫描（openspec: fleet-drift-and-hygiene / 对外字段卫生）。

**manifest 里的每一个字段都会被原样发布。** 这条不是推测：2026-11-18 实测线上
`8gqJKlQ7` v5 的整段 `_casting.authorNotes` 可读，里面写着「内部元团队：取数/
分析/押注三岗…」。我当时以为 `_casting` 是「仓内元数据、不对外投影」，错了——
`_casting` 是 manifest 的合法字段，市场照单全收。

所以凡是不该被终端用户读到的内容，都不能放在 manifest 的任何字段里，包括
看起来像内部用的 `_casting`。作者台账改放 `<pack>/NOTES.author.md`（旁挂文件，
不进 manifest）。

本判据扫的是**整个 manifest 文档**（不是 `check_drift.py` 只看 OUTWARD_FIELDS
那五个键——那道闸恰好漏掉 `_casting`，这次泄漏就是它看不见的那一类）：

  · 内部语汇：内部 / 私有 / internal / 真跑过再谈公开 / meta-team 之外的自述
  · 仓内路径与阶段词：fleet-packs/、openspec/、change 名、L0–L5
  · 基础设施：IP、NodePort、cell id、服务器名
  · 铸造台账：`_casting.authorNotes` 这类「作者写给自己的话」

**白名单**：`KNOWN_LEAKS`（按 pack 目录名）登记已确认且暂缓的项，每次打印。

用法：
    python3 scripts/check_publish_leak.py
    python3 scripts/check_publish_leak.py --self-test   # 六条负向对照臂
退出码：0=干净；1=有泄漏；2=判不了（仓根不存在）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 扫整个文档（含 _casting / i18n / fleet / agents / skills 正文）。
#
# ⚠️ 第一版把英文 `internal` 也当标记，结果 20 份里绝大多数是假红：
#   `internal-comms` 这个**技能名本身就是**「内部沟通写作」（Anthropic 官方技能，
#   公开的），`mcp-builder` 的教学文本里写着 "Don't expose internal errors to
#   clients"（那是**安全建议**，恰恰相反），`skill-creator` 里有 "internal
#   Anthropic data"（举反例）。用弱来源判红就是伪造证据——这句话本仓写过很多次，
#   这次自己犯。
#
# 所以判据只收「明确是保密标记」的形态：
#   · 中文「内部/私有」+ 紧跟组织/团队/使用 这类搭配（内部元团队 / 私有部署 / 内部使用）
#   · 明确的保密自述短语（真跑过再谈公开 / 不对外 / 不进公开面）
#   · 仓内路径与阶段词、基础设施标识、作者台账字段名
# 单说「内部沟通」「受内部限制」这类正当语义不判。
LEAK_RE = re.compile(
    r"内部(元团队|团队|使用|部署|试用)|私有(部署|仓|版本)|真跑过再谈|不对外(公开|发布)|"
    r"不进公开|fleet-packs/|\bopenspec/|\b(?:paas|fd-wanxing|fd-wire)/openspec/|"
    r"\bL[0-5]\b|\b10\.\d+\.\d+\.\d+\b|\b100\.64\.\b|NodePort|cheap-\d|"
    r"\bcell[ _-]?[0-9a-f]{8}\b|\b(?:add|fix|retire|remove|archive|unify|harden|adopt)-[a-z0-9]+(?:-[a-z0-9]+){1,}\b"
)

# 作者台账式的字段名：这类键一旦出现在 manifest 里就是「写给自己的话被发布」。
AUTHOR_KEYS = ("authorNotes", "author_notes", "authorNotesZh")

KNOWN_LEAKS: dict[str, str] = {}


def scan_manifest(doc, path: str = "") -> list[tuple[str, str]]:
    """递归找泄漏点，返回 [(字段路径, 命中的文本片段)]。"""
    hits: list[tuple[str, str]] = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            here = f"{path}.{k}" if path else k
            if k in AUTHOR_KEYS:
                hits.append((here, f"作者台账字段 {k}（内容不对外，改放 NOTES.author.md）"))
                continue
            # tag 里的「内部」是结构描述词还是保密标记？本仓裁定：内部/私有
            # 在 tags 里也是对外词，必须去掉（2026-11-18 三支元线就是这么洗的）。
            hits.extend(scan_manifest(v, here))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            hits.extend(scan_manifest(v, f"{path}[{i}]"))
    elif isinstance(doc, str):
        for m in LEAK_RE.finditer(doc):
            hits.append((path, f"…{doc[max(0, m.start()-25):m.end()+25]}…"))
    return hits


def check(root: Path) -> tuple[int, int]:
    if not root.is_dir():
        print(f"❌ 仓根不存在：{root}", file=sys.stderr)
        return (-1, 0)

    red = 0
    scanned = 0
    for depth in ("*/*", "*/*/*", "*/*/*/*"):
        for f in sorted(root.glob(f"{depth}/manifest.json")):
            if "/.git/" in str(f):
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"⚠️  {f}: 不是合法 JSON（{e}）", file=sys.stderr)
                continue
            scanned += 1
            slug = f.parent.name
            hits = scan_manifest(doc)
            if not hits:
                continue
            if slug in KNOWN_LEAKS:
                print(f"➖ {slug}（暂缓：{KNOWN_LEAKS[slug]}）{len(hits)} 处")
                continue
            print(f"❌ {slug}: {len(hits)} 处泄漏")
            for where, what in hits[:6]:
                print(f"     · {where}: {what}")
            if len(hits) > 6:
                print(f"     …另有 {len(hits)-6} 处")
            red += 1

    print(f"\n合计扫了 {scanned} 份 manifest，{red} 份有泄漏"
          f"（{len(KNOWN_LEAKS)} 个登记暂缓）")
    return (red, 0)


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    import contextlib
    import io
    import shutil

    def build(root: Path, packs: dict[str, dict]) -> None:
        for slug, m in packs.items():
            d = root / "cat" / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

    cases = [
        ("A 干净 → 绿", 0, {"pkg-a": {"name": "团队", "notes": "面向用户的一段话"}}),
        ("B 顶层 notes 含「内部元团队」→ 红", 1, {"pkg-a": {"name": "团队", "notes": "内部元团队：三岗…"}}),
        ("C _casting.authorNotes → 红", 1, {"pkg-a": {"name": "团队", "_casting": {"authorNotes": "随便什么"}}}),
        ("D notes 含「不对外发布」→ 红", 1, {"pkg-a": {"name": "团队", "notes": "本包不对外发布"}}),
        ("E 路径泄漏 → 红", 1, {"pkg-a": {"name": "团队", "notes": "来源 fleet-packs/lex/x"}}),
        # 反向臂：英文 internal 的正当语义（安全建议、技能名）不许判红。
        # 第一版判据把 20 份里绝大多数判成假红，全是这一类。
        ("G 正当英文 internal 不判红", 0, {"pkg-a": {"name": "Internal Comms",
          "notes": "Don't expose internal errors to clients", "tags": ["internal-comms"]}}),
    ]
    passed = 0
    for name, want_red, packs in cases:
        root = Path(tempfile.mkdtemp(prefix="leak-"))
        try:
            build(root, packs)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(root)
            ok = red == want_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（红 {red}/{want_red}）")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # F：豁免生效（登记后不判红，但仍打印）
    root = Path(tempfile.mkdtemp(prefix="leak-"))
    try:
        build(root, {"pkg-a": {"name": "团队", "notes": "内部"}})
        saved = dict(KNOWN_LEAKS)
        KNOWN_LEAKS["pkg-a"] = "self-test 豁免"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(root)
            out = buf.getvalue()
            ok = red == 0 and "暂缓" in out
        finally:
            KNOWN_LEAKS.clear(); KNOWN_LEAKS.update(saved)
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 豁免生效且被打印（红 {red}）")
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
    red, _ = check(Path(args.root).resolve())
    if red == -1:
        return 2
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
