#!/usr/bin/env python3
"""在架条目的 en 副轨覆盖体检（发布前跑；只读）。

抓的是「官方发布被 `official_en_coverage_incomplete` 拒，而没人知道到底缺哪几个字段」。
真源是平台的叶模块 `paas/lib/pack-locale-coverage.js`（`localeCoverage` / `orgSurface`），
本脚本只做两件它不做的事：把结果翻成人读的「缺哪条、该写在哪个键」清单，并给出**离线可复跑**
的退出码。

覆盖面的定义（照源码，不猜）：`manifest` 上声明的**展示字段**才算义务——
`name`/`description`/`notes`（标量）、`promise`、`tags`（整数组对齐）、
`skills['<name>'].description` 与 `.name`（声明了 `title` 才有 name 义务）、
`agents['<id>'].name`、`bots['<id>'].name`、`guide.steps[i].title|body`、
`outcomes[i].title|detail`、`examples[i].title|prompt`。
对应物写在**顶层** `i18n.en` 里：标量同名键、`skills`/`agents`/`bots` 是按声明名/ id 索引的
对象、`tags` 是等长数组。**不是**「description 对象化（zh＋en 并置）」——2026-11-14 实测：
那样写不会被识别，判词照样点名 `skills['<name>'].description`。

用法：
    python3 tooling/check_en_coverage.py <manifest.json> [--paas /path/to/paas]
    python3 tooling/check_en_coverage.py <manifest.json> --self-test
退出码：0=全覆盖；1=有缺项（逐条点名）；2=判不出（拿不到 paas 模块或 node）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JOB = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const m = JSON.parse(readFileSync(process.argv[3], "utf8"));
const cov = mod.localeCoverage(m, "en");
const gate = mod.officialEnGateBody(m);
console.log(JSON.stringify({ cov, gateNull: gate === null }));
"""

HINT = {
    "name": '`i18n.en.name`（字符串）',
    "description": '`i18n.en.description`（字符串）',
    "notes": '`i18n.en.notes`（字符串）',
    "promise": '`i18n.en.promise`（字符串）',
    "tags": '`i18n.en.tags`（与顶层 tags 等长的字符串数组）',
}


def _hint(path: str) -> str:
    if path in HINT:
        return HINT[path]
    if path.startswith("skills['"):
        name, _, field = path[6:].partition("'].")
        return f'`i18n.en.skills["{name}"].{field}`'
    if path.startswith("agents['"):
        aid, _, field = path[7:].partition("'].")
        return f'`i18n.en.agents["{aid}"].{field}`'
    if path.startswith("bots['"):
        bid, _, field = path[5:].partition("'].")
        return f'`i18n.en.bots["{bid}"].{field}`'
    if path.startswith("guide.steps["):
        return "`i18n.en.guide.steps[<同一序号>].title|body`"
    if path.startswith(("outcomes[", "examples[")):
        return "`i18n.en.<同一数组>[<同一序号>].<同一子字段>`"
    return "（未知路径形态——先看 paas/lib/pack-locale-coverage.js 的 orgSurface）"


def _module(paas: Path) -> Path | None:
    cands = [paas / "lib" / "pack-locale-coverage.js",
             paas / "dist-opensource" / "platform" / "lib" / "pack-locale-coverage.js",
             paas / "dist-opensource" / "publish" / "lib" / "pack-locale-coverage.js"]
    return next((c for c in cands if c.exists()), None)


def run(manifest: Path, paas: Path) -> int:
    mod = _module(paas)
    if mod is None:
        print(f"✗ 判不出：paas 检出里没有 pack-locale-coverage.js（试过 {[str(c) for c in cands]}）"
              f"——用 --paas 指向 paas 检出", file=sys.stderr)
        return 2
    node = shutil.which("node")
    if not node:
        print("✗ 判不出：PATH 里没有 node", file=sys.stderr)
        return 2
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(JOB)
        script = fh.name
    out = subprocess.run([node, script, str(mod), str(manifest)],
                         capture_output=True, text=True)
    Path(script).unlink(missing_ok=True)
    if out.returncode != 0:
        print(f"✗ 判不出：node 跑真源模块失败\n{out.stderr.strip()[:400]}", file=sys.stderr)
        return 2
    data = json.loads(out.stdout)
    cov = data["cov"]
    print(f"en 覆盖 {cov['covered']}/{cov['total']}"
          f"{'（门已开：officialEnGateBody=null）' if data['gateNull'] else ''}")
    if not cov["missing"]:
        print("✅ 全覆盖")
        return 0
    for p in cov["missing"]:
        print(f"✗ 缺 {p}  → 写 {_hint(p)}")
    return 1


def self_test(paas: Path) -> int:
    if _module(paas) is None:
        print(f"✗ 判不出：{paas} 下没有 pack-locale-coverage.js——自检无处可跑"
              f"（用 --paas 指向 paas 检出；这不是「自检红」，是「自检没跑」）", file=sys.stderr)
        return 2
    base = {
        "name": "t", "description": "d", "version": "0.1.0",
        "skills": [{"name": "s1", "description": "技能一"}],
        "agents": [{"id": "a1", "name": "岗一"}],
    }
    arms = []
    arms.append(("未写 i18n.en 必须红",
                 dict(base, i18n={"en": {}}), 1))
    arms.append(("只写顶层 name/description 仍必须红（skills/agents 还没覆盖）",
                 dict(base, i18n={"en": {"name": "T", "description": "D"}}), 1))
    full = dict(base, i18n={"en": {
        "name": "T", "description": "D",
        "skills": {"s1": {"description": "Skill one"}},
        "agents": {"a1": {"name": "Post one"}},
    }})
    arms.append(("补齐 skills/agents 后必须绿", full, 0))
    ok = 0
    with tempfile.TemporaryDirectory(prefix="fd-en-cov-") as td:
        for label, obj, want in arms:
            p = Path(td) / "m.json"
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            got = run(p, paas)
            good = got == want
            print(f"  {'PASS' if good else 'FAIL'}  {label}（期望 {want}，实际 {got}）")
            ok += 1 if good else 0
    total = len(arms)
    print(f"self-test {'绿' if ok == total else '红'} ({ok}/{total})")
    return 0 if ok == total else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="en 副轨覆盖体检（真源=paas/lib/pack-locale-coverage.js）")
    ap.add_argument("manifest", nargs="?", help="要检查的 manifest.json")
    ap.add_argument("--paas", default=str(REPO.parent / "finddata" / "paas"))
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    paas = Path(a.paas)
    if a.self_test:
        return self_test(paas)
    if not a.manifest:
        ap.error("需要 manifest.json（或 --self-test）")
    return run(Path(a.manifest), paas)


if __name__ == "__main__":
    sys.exit(main())
