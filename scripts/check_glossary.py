#!/usr/bin/env python3
"""对外词表判据（openspec: fleet-drift-and-hygiene / pack-taxonomy-and-naming）。

`pack-taxonomy-and-naming` 的 2026-10-29/30 裁定把对外词定为：编排单位=**智能体团队**、
安装单位=**智能体**、商店=**智能体市场**。但词表此前只有一句叮嘱，没有读者——
2026-11-18 实测客户端 locale 里还留着「素材市场」（`nav.agents`）、
「本机已装功能集」、「集群概况」等七处旧词。

本判据把词表变成可复跑的检查：白名单式断言——**每个受管键要么取正确的词，
要么在 EXEMPT 里显式登记豁免并说明理由**。新出现的违规即红。

为什么是白名单而不是全局搜词：「集群」在本仓是**产品单位术语**（CONTEXT.md
词条），不是禁词；禁的是把它当对外显示词用。全局 grep 会把它和正当用法
（内部文档、code 键）混在一起，得到一道常年红的闸。所以只查**声明过的键**。

范围：paas 的五语 `web/src/locales/*/common.json`。这是客户端可见文案的唯一来源
（`web/src` 里没有第二份）。代码键、路由、testid 一律不查（零翻译纪律）。

用法：
    python3 fleet-packs/scripts/check_glossary.py                # 默认查 paas
    python3 fleet-packs/scripts/check_glossary.py --paas-root X
    python3 fleet-packs/scripts/check_glossary.py --self-test    # 六条负向对照臂
退出码：0=全绿；1=有违规；2=判不了（paas 找不到 / locale 文件缺失）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCALES = ("zh-CN", "en", "es", "fr", "ja")

# 受管键 → 每语的期望值。改裁定就改这张表（而不是改代码里的散落字符串）。
# 只列「有明确裁定词」的键；文案长句不在表内（它们没有唯一正确措辞，
# 硬编码会变成拿作者文风当判据）。
EXPECTED: dict[str, dict[str, str]] = {
    "nav.agents": {
        "zh-CN": "智能体市场",
        "en": "Agent Market",
        "es": "Mercado de agentes",
        "fr": "Marché des agents",
        "ja": "エージェントマーケット",
    },
}

# 显式豁免：键 → 理由。豁免也要被读到，不能靠「不在表里」静默通过。
# 目前为空；加项必须写清为什么这个键不该跟词表。
EXEMPT: dict[str, str] = {}

# 旧词黑名单（受管键上出现即红，即便期望值表没覆盖该语）。
# 这些是 2026-10-29/30 裁定明确退场的词；出现在对外文案里就是没跟。
RETIRED = {
    "zh-CN": ("素材市场", "功能集"),
    "en": ("Materials",),
    "es": ("Materiales",),
    "fr": ("Matériels",),
    "ja": ("マテリアル",),
}


def dig(doc: dict, dotted: str):
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def check(paas_root: Path) -> tuple[int, int]:
    loc_dir = paas_root / "web" / "src" / "locales"
    if not loc_dir.is_dir():
        print(f"❌ locale 目录不存在：{loc_dir}——判不了", file=sys.stderr)
        return (-1, 0)

    docs: dict[str, dict] = {}
    for loc in LOCALES:
        f = loc_dir / loc / "common.json"
        if not f.exists():
            print(f"❌ 缺 {loc}/common.json——判不了", file=sys.stderr)
            return (-1, 0)
        try:
            docs[loc] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"❌ {loc}/common.json 不是合法 JSON（{e}）——判不了", file=sys.stderr)
            return (-1, 0)

    red = 0
    for key, want in EXPECTED.items():
        if key in EXEMPT:
            print(f"➖ {key} 已登记豁免：{EXEMPT[key]}")
            continue
        for loc in LOCALES:
            got = dig(docs[loc], key)
            if got is None:
                print(f"❌ {loc}: 键 {key} 不存在（词表要求它有值）")
                red += 1
            elif got != want[loc]:
                print(f"❌ {loc}: {key} = {got!r}，期望 {want[loc]!r}")
                red += 1
            else:
                print(f"✅ {loc}: {key} = {got!r}")

    # 退休词扫描：只扫受管键（理由见 docstring）。
    # 已登记豁免的键整段跳过——豁免是对「这个键」的豁免，不是只对期望值比对的豁免；
    # 两处各判一次会让豁免形同虚设（自测 F 臂抓到的就是这个）。
    for key in EXPECTED:
        if key in EXEMPT:
            continue
        for loc in LOCALES:
            got = dig(docs[loc], key)
            if not got:
                continue
            for bad in RETIRED.get(loc, ()):
                if bad in got:
                    print(f"❌ {loc}: {key} 含退场词 {bad!r}（{got!r}）")
                    red += 1
    return (red, 0)


# ── 负向对照 ─────────────────────────────────────────────────────────────

def self_test() -> int:
    import contextlib
    import io
    import shutil

    def build(root: Path, overrides: dict[tuple[str, str], str | None]) -> None:
        for loc in LOCALES:
            d = root / "web" / "src" / "locales" / loc
            d.mkdir(parents=True, exist_ok=True)
            doc = {"nav": {"agents": EXPECTED["nav.agents"][loc]}}
            for (l, k), v in overrides.items():
                if l != loc:
                    continue
                node = doc
                parts = k.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                if v is None:
                    node.pop(parts[-1], None)
                else:
                    node[parts[-1]] = v
            (d / "common.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    cases = [
        ("A 五语全正确 → 绿", 0, {}),
        ("B 一语旧词 → 红 2", 2, {("zh-CN", "nav.agents"): "素材市场"}),
        ("C 一语键缺失 → 红 1", 1, {("ja", "nav.agents"): None}),
        ("D 一语另一种错值 → 红 1", 1, {("en", "nav.agents"): "Packs"}),
    ]
    passed = 0
    for name, want_red, ov in cases:
        root = Path(tempfile.mkdtemp(prefix="glossary-"))
        try:
            build(root, ov)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(root)
            ok = red == want_red
            passed += ok
            print(f"  {'✅' if ok else '❌'} {name}（红 {red}/{want_red}）")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # E：locale 目录不存在 → 判不了
    root = Path(tempfile.mkdtemp(prefix="glossary-"))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            red, _ = check(root)
        ok = red == -1
        passed += ok
        print(f"  {'✅' if ok else '❌'} E locale 目录缺失 → 判不了（哨兵 {red}）")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # F：登记豁免后不判红（豁免必须真的生效，否则豁免机制是空话）
    root = Path(tempfile.mkdtemp(prefix="glossary-"))
    try:
        build(root, {("zh-CN", "nav.agents"): "素材市场"})
        saved = dict(EXEMPT)
        EXEMPT["nav.agents"] = "self-test 豁免"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                red, _ = check(root)
            out = buf.getvalue()
            ok = red == 0 and "已登记豁免" in out
        finally:
            EXEMPT.clear(); EXEMPT.update(saved)
        passed += ok
        print(f"  {'✅' if ok else '❌'} F 豁免生效且被打印（红 {red}）")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    total = len(cases) + 2
    print(f"\nself-test {'绿' if passed == total else '红'} ({passed}/{total})")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paas-root", default=str(REPO.parent / "paas"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    root = Path(args.paas_root).resolve()
    if not root.is_dir():
        print(f"❌ paas 仓不存在：{root}——判不了", file=sys.stderr)
        return 2
    red, _ = check(root)
    if red == -1:
        return 2
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
