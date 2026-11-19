#!/usr/bin/env python3
"""四语填充器：按 en 的**键形状**生成 es/fr/ja 的空壳，内容由调用方提供。

用法（作为库）：

    from fill_locales import fill
    fill(manifest_path, {
        'es': {'name': '...', 'description': '...', 'notes': '...', 'tags': [...],
               'agents': {'a-id': {'name': '...'}},
               'fleet': {'name': '...', 'members': {'ref': {'title': '...',
                            'kpiSlots': {'key': {'label': '...'}}}}}},
        'fr': {...}, 'ja': {...},
    })

它做三件事，都是**结构**上的机械工作，不代替翻译：

  1. 补齐 `i18n.{es,fr,ja}` 的顶层键（name/description/notes/tags），
     缺的键以既有 en 值占位并打标记，避免出现「键不存在」的门错。
  2. 补齐 `i18n.{loc}.agents[<id>].name` 与 `i18n.{loc}.skills[<name>].description`
     的键——**值必须由调用方给**，缺一个就抛错（不静默填英文，那会让
     「已翻译」与「未翻译」在发布面上不可区分）。
  3. 若 manifest 有 `fleet.members`，补齐 `fleet.i18n.{loc}.members[<ref>]`
     的 title 与 kpiSlots.label 键，同样要求调用方给值。

为什么单独成文件而不是内联在某次会话的脚本里：这 20 个包的补齐要分批做
（一次做不完），下一个人接手时需要同一个工具。它只依赖 stdlib。

退出码：作为脚本跑 `--check <manifest>` 时，列出还缺的翻译键，0=齐全 1=有缺。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOCALES = ("es", "fr", "ja")
MISSING = "‹待译›"


class MissingTranslation(Exception):
    pass


def _write(path: Path, doc: dict) -> None:
    raw = path.read_text(encoding="utf-8")
    m = re.search(r'\n(\s+)"', raw)
    ind = len(m.group(1)) if m else 2
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=ind) + ("\n" if raw.endswith("\n") else ""),
        encoding="utf-8",
    )


def required_keys(doc: dict) -> dict[str, list[str]]:
    """从 manifest 推出「每个 locale 必须提供哪些值」的形状。

    返回 {loc: [路径描述]}，用于 --check 与 fill 的完备性校验。路径用点号形式，
    例如 `agents.erp-credit-fleet-watcher.name`、`fleet.members.watcher.kpiSlots.released.label`。
    """
    need: list[str] = ["name", "description", "notes"]
    for a in doc.get("agents") or []:
        if a.get("id"):
            need.append(f"agents.{a['id']}.name")
    for s in doc.get("skills") or []:
        if s.get("name"):
            need.append(f"skills.{s['name']}.description")
    fleet = doc.get("fleet") or {}
    if fleet.get("name"):
        # 发布门要求 fleet.i18n.<loc>.name 也有译名。第一版漏了这一项——
        # 六个 ERP 包补齐后仍被门拒，就是这么发现的。
        need.append("fleet.name")
    for mem in fleet.get("members") or []:
        ref = mem.get("ref")
        if not ref:
            continue
        need.append(f"fleet.members.{ref}.title")
        for slot in mem.get("kpiSlots") or []:
            if slot.get("key"):
                need.append(f"fleet.members.{ref}.kpiSlots.{slot['key']}.label")
    return {loc: need for loc in LOCALES}


def _get(node, dotted: str):
    cur = node
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    need = required_keys(doc)
    bad = 0
    for loc, keys in need.items():
        block = (doc.get("i18n") or {}).get(loc) or {}
        fleet_block = ((doc.get("fleet") or {}).get("i18n") or {}).get(loc) or {}

        def present(k: str) -> bool:
            # `fleet.members.X` 住在 `fleet.i18n.<loc>`，其余住在 `i18n.<loc>`。
            # 第一版把两句写成了两个独立推导，第一句把 fleet 键也拿去普通块里查，
            # 于是所有 fleet 键恒「缺失」——自测当场抓到。
            if k.startswith("fleet."):
                return bool(_get(fleet_block, k.replace("fleet.", "", 1)))
            return bool(_get(block, k))

        miss = [k for k in keys if not present(k)]
        if miss:
            bad += 1
            print(f"  {loc}: 缺 {len(miss)} 项")
            for k in miss[:8]:
                print(f"     · {k}")
            if len(miss) > 8:
                print(f"     …另有 {len(miss)-8} 项")
        else:
            print(f"  ✅ {loc}: 齐全")
    return 0 if bad == 0 else 1


def fill(path: Path, values: dict[str, dict]) -> None:
    """按调用方给的值填充。缺任何 required key 都抛 MissingTranslation。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    need = required_keys(doc)
    i18n = doc.setdefault("i18n", {})
    fleet_i18n = (doc.get("fleet") or {}).setdefault("i18n", {}) if doc.get("fleet") else {}

    problems: list[str] = []
    for loc in LOCALES:
        given = values.get(loc) or {}
        block = i18n.setdefault(loc, {})
        fblock = fleet_i18n.setdefault(loc, {}) if doc.get("fleet") else {}
        for key in need[loc]:
            if key.startswith("fleet."):
                sub = key.replace("fleet.", "", 1)
                parts = sub.split(".")
                src = given.get("fleet") or {}
                val = _get(src, sub)
                if not val:
                    problems.append(f"{loc}:{key}")
                    continue
                # 写进 fleet.i18n
                node = fblock
                for p in parts[:-1]:
                    node = node.setdefault(p, {})
                node[parts[-1]] = val
            else:
                val = _get(given, key)
                if not val:
                    problems.append(f"{loc}:{key}")
                    continue
                node = block
                parts = key.split(".")
                for p in parts[:-1]:
                    node = node.setdefault(p, {})
                node[parts[-1]] = val
    if problems:
        raise MissingTranslation("缺翻译键：" + ", ".join(problems[:12]))

    _write(path, doc)


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        return check(Path(sys.argv[2]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
