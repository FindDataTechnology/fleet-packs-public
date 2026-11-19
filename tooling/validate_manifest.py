#!/usr/bin/env python3
"""Validate facet pack manifests before publishing.

Prefers the REAL validator: if a paas checkout is available, this shells out to
`lib/pack-manifest.js` (validatePackManifest) via node, so the rules can never
drift from what the publish endpoint enforces. Falls back to a mirrored rule set
(kept in lockstep with paas `lib/pack-manifest.js`, read 2026-10-10) when node
or the paas repo is missing.

Usage:
    python3 validate_manifest.py <manifest.json> [<manifest2.json> ...]
    python3 validate_manifest.py --paas /path/to/paas <manifest.json>
    python3 validate_manifest.py --mirror <manifest.json>   # force built-in rules
    python3 validate_manifest.py --require-locales en,ja,es,fr <manifest.json>
        # release gate: every required secondary track must cover every declared
        # display field (top-level i18n + fleet.i18n); fleet.i18n is also
        # structurally validated in every mode (paas never inspects `fleet`).
    python3 validate_manifest.py --no-live <manifest.json>
        # skip the registry tool-surface fetch (offline); allowTools is then
        # printed as UNVERIFIED instead of being checked
    python3 validate_manifest.py --strict-allowtools <manifest.json>
        # fail (not just warn) when the tool surface cannot be fetched
    python3 validate_manifest.py --all [--all-root DIR] [--no-live]
        # 扫仓内**全部** manifest.json（跳过 _parts/ dist/ __pycache__/
        # node_modules/ 与点目录），逐份打判词 + 一行汇总 `N 份 / 有错 a / 降级 b`。
        # 人口为 0 ⇒ 退出码 2（「扫到 0 份」不是通过）
    python3 validate_manifest.py --self-test
        # 离线自证 --all 的人口/汇总/降级/排除语义（不联网、不碰 registry）

Exit code: 0 = all valid; 1 = at least one invalid; 2 = usage/IO error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── limits (mirror paas PACK_LIMITS, lib/pack-manifest.js) ────────────────────
MAX_SKILLS = 10
MAX_SKILL_BODY = 64 * 1024
# paas lib/pack-manifest.js 的 agents 限额是 env 驱动（PACK_MAX_AGENTS，映射见
# limits-map：maxAgents→"PACK_MAX_AGENTS"）；生产已设 40（fd-infra-deploy
# platform-config，2026-10-25 起，四岗舰队模板的余量）。镜像规则不再钉死 3：
# 无 env 时按生产值 40 兜底，宁可漏报也不假拒（真校验器是权威，见下）。
import os as _os
MAX_AGENTS = int(_os.environ.get("PACK_MAX_AGENTS") or 40)
MAX_MCP_REFS = 5
MAX_TAGS = 10
MAX_TAG_CHARS = 24
MAX_NAME_CHARS = 80
MAX_DESCRIPTION_CHARS = 2000
MAX_PERSONA_CHARS = 16 * 1024
MAX_AGENT_NAME_CHARS = 60
MAX_SKILL_DESC_CHARS = 300
MAX_SKILL_TITLE_CHARS = 60  # add-skill-supply-annotations: skills[].title zh base display name
# Locale overlay (add-pack-locale-tracks; mirror paas LOCALE_* rules)
MAX_NOTES_CHARS = 500
MAX_GUIDE_TITLE_CHARS = 60
MAX_GUIDE_BODY_CHARS = 500
MAX_CARD_NAME_CHARS = 100
MAX_CARD_DESC_CHARS = 1000
MAX_CAP_DESC_CHARS = 500
MAX_CLUSTER_NAME_CHARS = 100
MAX_CLUSTER_DESC_CHARS = 500
MAX_MCP_DESC_CHARS = 300
# Fleet template overlay (pack-locale-supply): paas lib/pack-locale.js projects
# `fleet.i18n`, but paas validatePackManifest never inspects `fleet` — a broken
# overlay would publish clean and silently render zh. These caps mirror the
# base display fields it covers.
MAX_FLEET_NAME_CHARS = 80
MAX_FLEET_TITLE_CHARS = 60
MAX_KPI_LABEL_CHARS = 60
LOCALE_LENGTH_RATIO = 3
LOCALE_LENGTH_FLOOR = 20

CJK_WIDTH_RE = re.compile(
    "[\u1100-\u115F\u2E80-\uA4CF\uA960-\uA97F\uAC00-\uD7A3\uF900-\uFAFF"
    "\uFE10-\uFE19\uFE30-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6\u3000-\u303F"
    "\u3040-\u30FF\u3190-\u319F\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF]"
)


def _display_width(value: str) -> int:
    """CJK/fullwidth chars count 2, everything else 1 (lockstep with paas)."""
    return sum(2 if CJK_WIDTH_RE.match(ch) else 1 for ch in value)


LOCALE_OVERLAY_KEYS = ("name", "description", "notes", "guide", "skills", "mcpServers", "agents", "bots", "cluster")
LOCALE_KEY_RE = re.compile(r"^[a-z]{2,3}(-[a-zA-Z0-9]{2,8})*$")
# Same rule as finddata docs/marketing/audit.py ABSOLUTE_CLAIM_RE (lockstep).
ABSOLUTE_CLAIM_RE = re.compile(
    r"(?:接管|替代|取代)(?:所有|全部|一切)(?:人类)?(?:的)?工作|全面(?:接管|替代|取代)|"
    r"replac\w+\s+all\s+(?:human\s+)?work|tak\w+\s+over\s+all\s+(?:human\s+)?work",
    re.IGNORECASE,
)
# Skill package files (add-packaged-skills; mirror paas PACK_LIMITS + lib/skill-files.js)
MAX_SKILL_FILE_CHARS = 512 * 1024
MAX_SKILL_AUX_FILES = 64
MAX_SKILL_AUX_CHARS = 2 * 1024 * 1024
TEXT_EXTENSIONS = {
    "md", "txt", "json", "yaml", "yml", "toml", "csv", "tsv",
    "py", "sh", "bash", "zsh", "js", "mjs", "cjs", "ts", "rb", "pl", "ps1", "bat", "cmd",
    "sql", "html", "css", "ini", "cfg", "conf", "env", "gitignore", "dockerfile", "xml",
}
TEXT_FILENAMES = {"Dockerfile", "Makefile"}

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MCP_FORBIDDEN_KEYS = ("url", "command", "args", "env", "headers", "config", "configTemplate")
AGENT_FORBIDDEN_KEYS = ("baseUrl", "model", "apiKey", "apiKeyEnv", "local", "url")

DEFAULT_PAAS_CANDIDATES = (
    os.environ.get("PAAS_REPO", ""),
    "/Users/chengsishi/finddata/paas",
)


def find_paas(explicit: str | None) -> Path | None:
    # An explicit --paas is authoritative: a missing path means "use mirrored
    # rules", never "fall back to a different checkout the caller didn't name".
    if explicit is not None:
        p = Path(explicit)
        return p if (p / "lib" / "pack-manifest.js").exists() else None
    for cand in DEFAULT_PAAS_CANDIDATES:
        if cand and (Path(cand) / "lib" / "pack-manifest.js").exists():
            return Path(cand)
    return None


def validate_with_real(paas: Path, manifest_path: Path) -> list[dict]:
    """Shell out to node + the real validatePackManifest. Returns error list."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found")
    script = (
        "import { validatePackManifest } from "
        f"'{paas / 'lib' / 'pack-manifest.js'}';"
        "import { readFileSync } from 'node:fs';"
        f"const m = JSON.parse(readFileSync({json.dumps(str(manifest_path.resolve()))}, 'utf8'));"
        "const errors = validatePackManifest(m);"
        "console.log(JSON.stringify(errors));"
    )
    # PACK_MAX_AGENTS 必须透传给子进程：真校验器的 agents 限额是 env 驱动
    # （limits-map maxAgents→"PACK_MAX_AGENTS"，默认 3）。生产已设 40——
    # 四岗舰队模板不透传会被子进程假拒（2026-10-25 实证）。
    env = {**os.environ, "PACK_MAX_AGENTS": os.environ.get("PACK_MAX_AGENTS") or "40"}
    out = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60, cwd=str(paas), env=env,
    )
    if out.returncode != 0:
        raise RuntimeError(f"node validator failed: {out.stderr.strip()[:400]}")
    return json.loads(out.stdout.strip() or "[]")


# ── allowTools 实探闸（fix-fleet-replay-dispatch-and-turn-accounting 3.2）──────
#: 声明的 allowTools 名字必须在 registry **实测**工具面上存在。以前只校验
#: registryName 的形状，拼错或写不存在的工具名会一路发布成功、装到人设里静默
#: 失效——2026-11-08 酒店线实证：人设报告「`hotel_*` 不在本角色启用工具集内」，
#: 而真正的原因是声明里只有四个通用名，没有任何一道闸会说话。
#:
#: 取数走 registry pod（spine 投影优先，mongo 副本兜底），与
#: supply-annotations/validate.py 同一套两条腿：spine 是 SPINE_REPLICA_MODE=1
#: 之后真正对外服务的读面，mongo 是切换前的副本（可能缺后加的工具）。
_KUBECTL_NS = "mcp-registry"
_KUBECTL_CTX = os.environ.get("FD_KUBECTL_CTX", "cheap-ts")
_KUBECTL_SELECTOR = "app.kubernetes.io/name=registry"

_SPINE_TOOLS_SCRIPT = r"""
import os, json, urllib.request
url = os.environ['SPINE_PROJECTION_URL'].rstrip('/') + '/api/supply-store/projection'
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['SPINE_PROJECTION_TOKEN']})
d = json.load(urllib.request.urlopen(req, timeout=30))
out = {}
for s in d.get('mcpServers') or []:
    sid = str(s.get('id') or '').lstrip('/')
    if not sid:
        continue
    out[sid] = [(t.get('name') if isinstance(t, dict) else str(t)) for t in (s.get('tool_list') or [])]
print(json.dumps({'source': 'spine', 'asOf': d.get('asOf'), 'servers': out}))
"""

_MONGO_TOOLS_SCRIPT = r"""
import os, json
from pymongo import MongoClient
u=os.environ['DOCUMENTDB_USERNAME']; p=os.environ['DOCUMENTDB_PASSWORD']
h=os.environ['DOCUMENTDB_HOST']; port=os.environ['DOCUMENTDB_PORT']; dbn=os.environ['DOCUMENTDB_DATABASE']
c=MongoClient(f'mongodb://{u}:{p}@{h}:{port}/{dbn}?authSource=admin', serverSelectionTimeoutMS=4000)
db=c[dbn]
out={}
for r in db['mcp_servers_default'].find({}, {'_id':1,'tool_list':1}):
    name=str(r['_id']).lstrip('/')
    out[name]=[ (t.get('name') if isinstance(t,dict) else str(t)) for t in (r.get('tool_list') or []) ]
print(json.dumps({'source': 'mongo', 'asOf': None, 'servers': out}))
"""

#: 最近一次成功取数的来源（打印它——「静默回退」不再无声）。
TOOL_SOURCE_NOTE = ""


def _registry_pod() -> str | None:
    """按 label 现取 Running 的 registry pod。滚动即变，写死必失配。"""
    try:
        proc = subprocess.run(
            ["kubectl", "--context", _KUBECTL_CTX, "-n", _KUBECTL_NS, "get", "pods",
             "-l", _KUBECTL_SELECTOR, "--field-selector=status.phase=Running",
             "-o", "jsonpath={.items[0].metadata.name}"],
            capture_output=True, text=True, timeout=30,
        )
        name = proc.stdout.strip()
        return name or None
    except Exception:
        return None


def live_tool_surfaces() -> dict[str, list[str]] | None:
    """只读拉取生产 registry 每个 server 的实测工具名（spine 优先，mongo 兜底）。"""
    global TOOL_SOURCE_NOTE
    pod = _registry_pod()
    if not pod:
        TOOL_SOURCE_NOTE = f"registry pod 未取到（context={_KUBECTL_CTX} ns={_KUBECTL_NS}）"
        return None
    for script, label in ((_SPINE_TOOLS_SCRIPT, "spine 投影"), (_MONGO_TOOLS_SCRIPT, "mongo 副本")):
        try:
            proc = subprocess.run(
                ["kubectl", "--context", _KUBECTL_CTX, "-n", _KUBECTL_NS, "exec", pod,
                 "--", "/app/.venv/bin/python3", "-c", script],
                capture_output=True, text=True, timeout=90,
            )
            if proc.returncode != 0:
                continue
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            servers = payload.get("servers") or {}
            if not servers:
                continue
            as_of = payload.get("asOf")
            TOOL_SOURCE_NOTE = f"{label}@{pod}" + (f"（asOf {as_of}）" if as_of else "")
            return servers
        except Exception:
            continue
    TOOL_SOURCE_NOTE = f"两条读面皆失败（{pod}）"
    return None


def validate_allow_tools(
    manifest: dict,
    surfaces: dict[str, list[str]] | None,
    fail,
    warn,
    unverified: list[str] | None = None,
) -> list[str]:
    """每个 allowTools 名字都必须在该 server 实测工具面上；返回已核 server 名。

    `unverified` 是**出参**：凡是「有声明、但没能核」的 entry 都追加一条原因（读面
    不可用／registry 里没有这个 server）。`--all` 的汇总行要数「降级 b」，而它此前
    只能从 warn 文案里反推——文案是给人看的，判据不该长在它上面。
    """
    checked: list[str] = []
    mcp = manifest.get("mcpServers")
    if not isinstance(mcp, list):
        return checked
    for i, m in enumerate(mcp):
        if not isinstance(m, dict):
            continue
        tools = m.get("allowTools")
        rn = m.get("registryName")
        if tools is None:
            continue
        entry = f"mcpServers[{i}].allowTools"
        if not isinstance(tools, list) or not tools:
            fail("allowTools must be a non-empty array of tool names", entry)
            continue
        if not all(isinstance(t, str) and t.strip() for t in tools):
            fail("allowTools entries must be non-empty strings", entry)
            continue
        if not isinstance(rn, str) or not rn:
            continue  # registryName itself is reported by the base rules
        if surfaces is None:
            warn(f"allowTools 未校验（{TOOL_SOURCE_NOTE or '读面不可用'}）", entry)
            if unverified is not None:
                unverified.append(f"{entry}: 读面不可用")
            continue
        live = surfaces.get(rn)
        if live is None:
            warn(f"registry has no server '{rn}' — allowTools 未校验", entry)
            if unverified is not None:
                unverified.append(f"{entry}: registry has no server '{rn}'")
            continue
        checked.append(rn)
        missing = [t for t in tools if t not in set(live)]
        if missing:
            fail(
                "allowTools declares tool(s) not on the live tool surface of "
                f"'{rn}': {', '.join(missing)} — live surface has {len(live)} tool(s)"
                + (f" ({', '.join(sorted(live)[:12])}…)" if len(live) > 12 else f" ({', '.join(sorted(live))})"),
                entry,
            )
    return checked


def _locale_override_ok(value, cap: int, entry: str, fail) -> bool:
    # Lockstep with paas lib/pack-manifest.js: overlay caps are enforced on
    # CJK-equivalent display widths (cap x2) so Latin tracks get visual room
    # equal to the CJK main track instead of failing at half the natural
    # expansion of a faithful translation.
    if not isinstance(value, str) or not value.strip() or _display_width(value) > cap * 2:
        fail(f"must be a non-empty string within {cap} CJK-equivalent display widths", entry)
        return False
    return True


def validate_locale_overlay(manifest: dict, fail) -> None:
    """Mirror of paas lib/pack-manifest.js validateLocaleOverlay (lockstep).

    Base display fields are the zh main track; the optional top-level `i18n`
    block only covers declared fields, named entities the way the manifest
    declares them (skills by name, agents/bots by id, card skills by id), the
    guide as a whole three-step group, with long-form 3:1 parity (short-string
    floor) and the en-track absolute-claim scan.
    """
    overlay = manifest.get("i18n")
    if not isinstance(overlay, dict):
        fail("i18n must be an object mapping locale tags to display-field overrides", "i18n")
        return
    if not overlay:
        fail("i18n must carry at least one locale block (omit the key instead of an empty object)", "i18n")
        return
    skills = {
        s.get("name"): s
        for s in manifest.get("skills", []) or []
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    agents = {
        a.get("id"): a
        for a in manifest.get("agents", []) or []
        if isinstance(a, dict) and isinstance(a.get("id"), str)
    }
    bots = {
        b.get("id"): b
        for b in manifest.get("bots", []) or []
        if isinstance(b, dict) and isinstance(b.get("id"), str)
    }

    for locale, block in overlay.items():
        locale = str(locale)
        lentry = f"i18n.{locale}"
        if not LOCALE_KEY_RE.match(locale):
            fail(f"i18n locale '{locale}' must be a lower-case BCP-47 tag ([a-z]{{2,3}} plus optional -suffixes)", "i18n")
            continue
        if locale == "zh" or locale.startswith("zh-"):
            fail(f"i18n may not declare '{locale}' — Chinese is the main track and lives in the base string fields", "i18n")
            continue
        if not isinstance(block, dict):
            fail("must be an object of display-field overrides", lentry)
            continue
        for key in block:
            if key not in LOCALE_OVERLAY_KEYS:
                fail(f"has unknown key '{key}' (only {', '.join(LOCALE_OVERLAY_KEYS)})", lentry)

        en_track = locale == "en" or locale.startswith("en-")
        scanned: list[tuple[str, str]] = []
        ratios: list[tuple[str, str, str]] = []

        # Scalar display fields, each bound to the base field's own cap.
        for key, cap in (("name", MAX_NAME_CHARS), ("description", MAX_DESCRIPTION_CHARS), ("notes", MAX_NOTES_CHARS)):
            if key not in block:
                continue
            entry = f"{lentry}.{key}"
            base = manifest.get(key)
            if not isinstance(base, str) or not base.strip():
                fail(f"overrides '{key}', which the manifest does not declare in its zh main track", entry)
                continue
            if not _locale_override_ok(block[key], cap, entry, fail):
                continue
            scanned.append((entry, block[key]))
            if key == "description":
                ratios.append((entry, block[key], base))

        # Usage guide — the whole three-step group or nothing.
        if "guide" in block:
            gentry = f"{lentry}.guide"
            base_guide = manifest.get("guide")
            base_steps = base_guide.get("steps") if isinstance(base_guide, dict) else None
            g = block["guide"]
            if not isinstance(base_steps, list) or len(base_steps) != 3:
                fail("overrides guide, which the manifest does not declare in its zh main track (three steps)", gentry)
            elif not isinstance(g, dict):
                fail("must be an object of { steps }", gentry)
            else:
                for key in g:
                    if key != "steps":
                        fail(f"guide has unknown key '{key}' (only steps)", gentry)
                steps = g.get("steps")
                if not isinstance(steps, list) or len(steps) != 3:
                    fail("guide.steps must be exactly 3 steps when overridden (translate the whole guide or omit it)", f"{gentry}.steps")
                else:
                    for i, st in enumerate(steps):
                        sentry = f"{gentry}.steps[{i}]"
                        if not isinstance(st, dict):
                            fail("guide step must be an object of { title, body }", sentry)
                            continue
                        for key in st:
                            if key not in ("title", "body"):
                                fail(f"guide step has unknown key '{key}' (only title, body)", sentry)
                        if _locale_override_ok(st.get("title"), MAX_GUIDE_TITLE_CHARS, f"{sentry}.title", fail):
                            scanned.append((f"{sentry}.title", st["title"]))
                        if _locale_override_ok(st.get("body"), MAX_GUIDE_BODY_CHARS, f"{sentry}.body", fail):
                            scanned.append((f"{sentry}.body", st["body"]))
                            base_step = base_steps[i] if isinstance(base_steps[i], dict) else {}
                            base_body = base_step.get("body")
                            if isinstance(base_body, str) and base_body:
                                ratios.append((f"{sentry}.body", st["body"], base_body))

        # Skill descriptions, keyed by declared skill name.
        if "skills" in block:
            if not isinstance(block["skills"], dict):
                fail("must be an object mapping declared skill names to { description }", f"{lentry}.skills")
            else:
                for name, ov in block["skills"].items():
                    entry = f"{lentry}.skills['{name}']"
                    if name not in skills:
                        fail(f"references '{name}', which the manifest does not declare in skills", entry)
                        continue
                    if not isinstance(ov, dict):
                        fail("must be an object of { description, name? }", entry)
                        continue
                    declared = skills.get(name) or {}
                    has_title = isinstance(declared.get("title"), str) and bool(declared["title"].strip())
                    for key in ov:
                        if key not in ("description", "name"):
                            fail(f"has unknown key '{key}' (only description and, for skills declaring a title, name)", entry)
                        elif key == "name" and not has_title:
                            fail(f"overrides 'name' but the skill entry declares no base title (add skills[].title first)", entry)
                    if "description" in ov and _locale_override_ok(ov["description"], MAX_SKILL_DESC_CHARS, f"{entry}.description", fail):
                        scanned.append((f"{entry}.description", ov["description"]))
                    if "name" in ov and _locale_override_ok(ov["name"], MAX_SKILL_TITLE_CHARS, f"{entry}.name", fail):
                        scanned.append((f"{entry}.name", ov["name"]))

        # Agent display names + their A2A card text, keyed by declared agent id.
        if "agents" in block:
            if not isinstance(block["agents"], dict):
                fail("must be an object mapping declared agent ids to overrides", f"{lentry}.agents")
            else:
                for aid, ov in block["agents"].items():
                    entry = f"{lentry}.agents['{aid}']"
                    agent = agents.get(aid)
                    if agent is None:
                        fail(f"references '{aid}', which the manifest does not declare in agents", entry)
                        continue
                    if not isinstance(ov, dict):
                        fail("must be an object of { name, serving }", entry)
                        continue
                    for key in ov:
                        if key not in ("name", "serving"):
                            fail(f"has unknown key '{key}' (only name, serving)", entry)
                    if "name" in ov and _locale_override_ok(ov["name"], MAX_AGENT_NAME_CHARS, f"{entry}.name", fail):
                        scanned.append((f"{entry}.name", ov["name"]))
                    if "serving" in ov:
                        sentry = f"{entry}.serving"
                        base_card = agent.get("serving", {}).get("card") if isinstance(agent.get("serving"), dict) else None
                        if not isinstance(ov["serving"], dict):
                            fail("must be an object of { card }", sentry)
                            continue
                        for key in ov["serving"]:
                            if key != "card":
                                fail(f"serving has unknown key '{key}' (only card)", sentry)
                        if "card" in ov["serving"]:
                            centry = f"{sentry}.card"
                            card_ov = ov["serving"]["card"]
                            if not isinstance(base_card, dict):
                                fail("overrides serving.card, which the manifest does not declare in its zh main track", centry)
                            elif not isinstance(card_ov, dict):
                                fail("must be an object of { name, description, skills }", centry)
                            else:
                                for ckey in card_ov:
                                    if ckey not in ("name", "description", "skills"):
                                        fail(f"card has unknown key '{ckey}' (only name, description, skills)", centry)
                                if "name" in card_ov and _locale_override_ok(card_ov["name"], MAX_CARD_NAME_CHARS, f"{centry}.name", fail):
                                    scanned.append((f"{centry}.name", card_ov["name"]))
                                if "description" in card_ov and _locale_override_ok(card_ov["description"], MAX_CARD_DESC_CHARS, f"{centry}.description", fail):
                                    scanned.append((f"{centry}.description", card_ov["description"]))
                                if "skills" in card_ov:
                                    base_card_skills = {
                                        cs.get("id"): cs
                                        for cs in (base_card.get("skills") or [])
                                        if isinstance(cs, dict) and isinstance(cs.get("id"), str)
                                    }
                                    if not isinstance(card_ov["skills"], dict):
                                        fail("must be an object mapping declared capability ids", f"{centry}.skills")
                                    else:
                                        for cid, csov in card_ov["skills"].items():
                                            csentry = f"{centry}.skills['{cid}']"
                                            if cid not in base_card_skills:
                                                fail(f"references '{cid}', which the manifest does not declare in serving.card.skills", csentry)
                                                continue
                                            if not isinstance(csov, dict):
                                                fail("must be an object of { name, description }", csentry)
                                                continue
                                            for ckey in csov:
                                                if ckey not in ("name", "description"):
                                                    fail(f"has unknown key '{ckey}' (only name, description)", csentry)
                                            if "name" in csov and _locale_override_ok(csov["name"], MAX_CARD_NAME_CHARS, f"{csentry}.name", fail):
                                                scanned.append((f"{csentry}.name", csov["name"]))
                                            if "description" in csov and _locale_override_ok(csov["description"], MAX_CAP_DESC_CHARS, f"{csentry}.description", fail):
                                                scanned.append((f"{csentry}.description", csov["description"]))

        # Bot display text, keyed by declared bot id.
        if "bots" in block:
            if not isinstance(block["bots"], dict):
                fail("must be an object mapping declared bot ids to { name, description }", f"{lentry}.bots")
            else:
                for bid, ov in block["bots"].items():
                    entry = f"{lentry}.bots['{bid}']"
                    if bid not in bots:
                        fail(f"references '{bid}', which the manifest does not declare in bots", entry)
                        continue
                    if not isinstance(ov, dict):
                        fail("must be an object of { name, description }", entry)
                        continue
                    for key in ov:
                        if key not in ("name", "description"):
                            fail(f"has unknown key '{key}' (only name, description)", entry)
                    if "name" in ov and _locale_override_ok(ov["name"], MAX_AGENT_NAME_CHARS, f"{entry}.name", fail):
                        scanned.append((f"{entry}.name", ov["name"]))
                    if "description" in ov and _locale_override_ok(ov["description"], MAX_SKILL_DESC_CHARS, f"{entry}.description", fail):
                        scanned.append((f"{entry}.description", ov["description"]))

        # MCP server display descriptions, keyed by the declared registryName
        # (add-mcp-annotation-overlay). Display only — registryName, tool-face
        # declaration and the rest of the runtime contract are never overlayable.
        if "mcpServers" in block:
            declared_mcp = {
                m.get("registryName"): m
                for m in manifest.get("mcpServers", []) or []
                if isinstance(m, dict) and isinstance(m.get("registryName"), str)
            }
            if not isinstance(block["mcpServers"], dict):
                fail("must be an object mapping declared registryNames to { description }", f"{lentry}.mcpServers")
            else:
                for rn, ov in block["mcpServers"].items():
                    entry = f"{lentry}.mcpServers['{rn}']"
                    if rn not in declared_mcp:
                        fail(f"references '{rn}', which the manifest does not declare in mcpServers", entry)
                        continue
                    if not isinstance(ov, dict):
                        fail("must be an object of { description }", entry)
                        continue
                    for key in ov:
                        if key != "description":
                            fail(f"has unknown key '{key}' (only description)", entry)
                    if "description" in ov and _locale_override_ok(ov["description"], MAX_MCP_DESC_CHARS, f"{entry}.description", fail):
                        scanned.append((f"{entry}.description", ov["description"]))

        # Cluster template display text.
        if "cluster" in block:
            centry = f"{lentry}.cluster"
            if not isinstance(manifest.get("cluster"), dict):
                fail("overrides cluster, which the manifest does not declare in its zh main track", centry)
            elif not isinstance(block["cluster"], dict):
                fail("must be an object of { name, description }", centry)
            else:
                for key in block["cluster"]:
                    if key not in ("name", "description"):
                        fail(f"has unknown key '{key}' (only name, description)", centry)
                if "name" in block["cluster"] and _locale_override_ok(block["cluster"]["name"], MAX_CLUSTER_NAME_CHARS, f"{centry}.name", fail):
                    scanned.append((f"{centry}.name", block["cluster"]["name"]))
                if "description" in block["cluster"] and _locale_override_ok(block["cluster"]["description"], MAX_CLUSTER_DESC_CHARS, f"{centry}.description", fail):
                    scanned.append((f"{centry}.description", block["cluster"]["description"]))

        # Long-form parity (both directions; skipped below the floor).
        for entry, value, base in ratios:
            value_width, base_width = _display_width(value), _display_width(base)
            if len(value) < LOCALE_LENGTH_FLOOR or len(base) < LOCALE_LENGTH_FLOOR:
                continue
            if value_width > base_width * LOCALE_LENGTH_RATIO or base_width > value_width * LOCALE_LENGTH_RATIO:
                fail(
                    f"is more than {LOCALE_LENGTH_RATIO}x its zh base in display width ({value_width} vs {base_width} width units) — check this is the translation, not different content",
                    entry,
                )

        # Absolute-replacement claim scan on the en secondary track.
        if en_track:
            for entry, value in scanned:
                if isinstance(value, str) and ABSOLUTE_CLAIM_RE.search(value):
                    fail("declares an absolute-replacement claim (honest-voice rule, same rule as the marketing audit)", entry)


def _fleet_members(fleet: dict) -> dict[str, dict]:
    return {
        m.get("ref"): m
        for m in (fleet.get("members") or [])
        if isinstance(m, dict) and isinstance(m.get("ref"), str)
    }


def _member_slots(member: dict) -> dict[str, dict]:
    return {
        s.get("key"): s
        for s in (member.get("kpiSlots") or [])
        if isinstance(s, dict) and isinstance(s.get("key"), str)
    }


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_fleet_i18n(manifest: dict, fail) -> None:
    """Validate `fleet.i18n` (pack-locale-supply).

    paas `lib/pack-locale.js` projects this block, but paas
    `validatePackManifest` never inspects `fleet` — a malformed overlay passes
    publish and silently renders the zh base. This local gate is the only
    structural guard. Shape (mirrors the projector):
      fleet.i18n.<locale> = { name?, members: { <ref>: { title, kpiSlots: { <key>: { label } } } } }
    """
    fleet = manifest.get("fleet")
    if not isinstance(fleet, dict):
        return
    overlay = fleet.get("i18n")
    if overlay is None:
        return
    if not isinstance(overlay, dict):
        fail("fleet.i18n must be an object mapping locale tags to display-field overrides", "fleet.i18n")
        return
    if not overlay:
        fail("fleet.i18n must carry at least one locale block (omit the key instead of an empty object)", "fleet.i18n")
        return
    members = _fleet_members(fleet)

    for locale, block in overlay.items():
        locale = str(locale)
        lentry = f"fleet.i18n.{locale}"
        if not LOCALE_KEY_RE.match(locale):
            fail(f"fleet.i18n locale '{locale}' must be a lower-case BCP-47 tag ([a-z]{{2,3}} plus optional -suffixes)", "fleet.i18n")
            continue
        if locale == "zh" or locale.startswith("zh-"):
            fail(f"fleet.i18n may not declare '{locale}' — Chinese is the main track and lives in the base strings", "fleet.i18n")
            continue
        if not isinstance(block, dict):
            fail("must be an object of { name, members }", lentry)
            continue
        for key in block:
            if key not in ("name", "members"):
                fail(f"has unknown key '{key}' (only name, members)", lentry)
        if "name" in block:
            _locale_override_ok(block["name"], MAX_FLEET_NAME_CHARS, f"{lentry}.name", fail)
        if "members" not in block:
            continue
        mov = block["members"]
        if not isinstance(mov, dict):
            fail("must be an object mapping declared member refs to { title, kpiSlots }", f"{lentry}.members")
            continue
        extra = sorted(set(mov) - set(members))
        if extra:
            fail(f"references undeclared member ref(s): {', '.join(extra)}", f"{lentry}.members")
        missing = sorted(set(members) - set(mov))
        if missing:
            fail(
                f"is a partial overlay — missing member ref(s): {', '.join(missing)} (translate every member or omit the key)",
                f"{lentry}.members",
            )
        for ref, mov_entry in mov.items():
            mentry = f"{lentry}.members['{ref}']"
            member = members.get(ref)
            if member is None:
                continue
            if not isinstance(mov_entry, dict):
                fail("must be an object of { title, kpiSlots }", mentry)
                continue
            for key in mov_entry:
                if key not in ("title", "kpiSlots"):
                    fail(f"has unknown key '{key}' (only title, kpiSlots)", mentry)
            if _nonempty_str(member.get("title")):
                if "title" not in mov_entry:
                    fail("is a partial overlay — missing title", mentry)
                else:
                    _locale_override_ok(mov_entry["title"], MAX_FLEET_TITLE_CHARS, f"{mentry}.title", fail)
            slots = _member_slots(member)
            if slots and "kpiSlots" not in mov_entry:
                fail("is a partial overlay — missing kpiSlots", mentry)
                continue
            if "kpiSlots" not in mov_entry:
                continue
            sov = mov_entry["kpiSlots"]
            if not isinstance(sov, dict):
                fail("must be an object mapping declared slot keys to { label }", f"{mentry}.kpiSlots")
                continue
            extra_slots = sorted(set(sov) - set(slots))
            if extra_slots:
                fail(f"references undeclared slot key(s): {', '.join(extra_slots)}", f"{mentry}.kpiSlots")
            missing_slots = sorted(set(slots) - set(sov))
            if missing_slots:
                fail(
                    f"is a partial overlay — missing slot key(s): {', '.join(missing_slots)} (translate every slot or omit the key)",
                    f"{mentry}.kpiSlots",
                )
            for sk, sv in sov.items():
                sentry = f"{mentry}.kpiSlots['{sk}']"
                if sk not in slots:
                    continue
                if not isinstance(sv, dict):
                    fail("must be an object of { label }", sentry)
                    continue
                for key in sv:
                    if key != "label":
                        fail(f"has unknown key '{key}' (only label)", sentry)
                if "label" not in sv:
                    fail("is a partial overlay — missing label", sentry)
                else:
                    _locale_override_ok(sv["label"], MAX_KPI_LABEL_CHARS, f"{sentry}.label", fail)


def _overlay_missing_top(manifest: dict, block: dict) -> list[str]:
    """Display-field paths the top-level `i18n` block must cover for one locale."""
    missing: list[str] = []
    for key in ("name", "description", "notes"):
        if _nonempty_str(manifest.get(key)) and not _nonempty_str(block.get(key)):
            missing.append(key)
    skills = {
        s.get("name"): s
        for s in (manifest.get("skills") or [])
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    sov = block.get("skills") if isinstance(block.get("skills"), dict) else {}
    for name, s in skills.items():
        ov = sov.get(name) if isinstance(sov, dict) else None
        if not isinstance(ov, dict) or not _nonempty_str(ov.get("description")):
            missing.append(f"skills['{name}'].description")
        if isinstance(s, dict) and _nonempty_str(s.get("title")) and not (isinstance(ov, dict) and _nonempty_str(ov.get("name"))):
            missing.append(f"skills['{name}'].name")
    agents = {
        a.get("id"): a
        for a in (manifest.get("agents") or [])
        if isinstance(a, dict) and isinstance(a.get("id"), str)
    }
    aov = block.get("agents") if isinstance(block.get("agents"), dict) else {}
    for aid in agents:
        ov = aov.get(aid) if isinstance(aov, dict) else None
        if not isinstance(ov, dict) or not _nonempty_str(ov.get("name")):
            missing.append(f"agents['{aid}'].name")
    guide = manifest.get("guide")
    steps = guide.get("steps") if isinstance(guide, dict) else None
    if isinstance(steps, list):
        gov = block.get("guide")
        gsteps = gov.get("steps") if isinstance(gov, dict) else None
        for i in range(len(steps)):
            ov = gsteps[i] if isinstance(gsteps, list) and i < len(gsteps) else None
            for key in ("title", "body"):
                if not isinstance(ov, dict) or not _nonempty_str(ov.get(key)):
                    missing.append(f"guide.steps[{i}].{key}")
    return missing


def _overlay_missing_fleet(manifest: dict, block: dict) -> list[str]:
    """Display-field paths `fleet.i18n` must cover for one locale."""
    fleet = manifest.get("fleet")
    if not isinstance(fleet, dict):
        return []
    missing: list[str] = []
    if _nonempty_str(fleet.get("name")) and not _nonempty_str(block.get("name")):
        missing.append("name")
    members = _fleet_members(fleet)
    mov = block.get("members") if isinstance(block.get("members"), dict) else {}
    for ref, member in members.items():
        ov = mov.get(ref) if isinstance(mov, dict) else None
        if not isinstance(ov, dict):
            if _nonempty_str(member.get("title")):
                missing.append(f"members['{ref}'].title")
            if _member_slots(member):
                missing.append(f"members['{ref}'].kpiSlots")
            continue
        if _nonempty_str(member.get("title")) and not _nonempty_str(ov.get("title")):
            missing.append(f"members['{ref}'].title")
        slots = _member_slots(member)
        sov = ov.get("kpiSlots") if isinstance(ov.get("kpiSlots"), dict) else {}
        for key in slots:
            label_ov = sov.get(key) if isinstance(sov, dict) else None
            if not isinstance(label_ov, dict) or not _nonempty_str(label_ov.get("label")):
                missing.append(f"members['{ref}'].kpiSlots['{key}'].label")
    return missing


def validate_locale_coverage(manifest: dict, locales: list[str]) -> list[dict]:
    """Release-gate coverage: every required secondary track covers every declared display field."""
    errors: list[dict] = []

    def fail(msg: str, entry: str | None = None) -> None:
        errors.append({"error": msg, **({"entry": entry} if entry else {})})

    top = manifest.get("i18n") if isinstance(manifest.get("i18n"), dict) else {}
    fleet = manifest.get("fleet")
    fleet_overlay = fleet.get("i18n") if isinstance(fleet, dict) and isinstance(fleet.get("i18n"), dict) else {}

    for locale in locales:
        block = top.get(locale)
        if not isinstance(block, dict):
            fail("missing locale block — the pack source must carry this secondary track", f"i18n.{locale}")
            continue
        for path in _overlay_missing_top(manifest, block):
            fail("missing display-field override", f"i18n.{locale}.{path}")
        if isinstance(fleet, dict):
            fblock = fleet_overlay.get(locale)
            if not isinstance(fblock, dict):
                fail("missing locale block — the fleet template must carry this secondary track", f"fleet.i18n.{locale}")
                continue
            for path in _overlay_missing_fleet(manifest, fblock):
                fail("missing display-field override", f"fleet.i18n.{locale}.{path}")
    return errors


def validate_mirrored(manifest: dict) -> list[dict]:
    """Mirrored rule set — keep in lockstep with paas lib/pack-manifest.js."""
    errors: list[dict] = []

    def fail(msg: str, entry: str | None = None) -> None:
        errors.append({"error": msg, **({"entry": entry} if entry else {})})

    if not isinstance(manifest, dict):
        return [{"error": "manifest must be an object"}]

    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME_CHARS:
        fail(f"name must be a non-empty string of at most {MAX_NAME_CHARS} characters")

    desc = manifest.get("description")
    if desc is not None and (not isinstance(desc, str) or len(desc) > MAX_DESCRIPTION_CHARS):
        fail(f"description must be a string of at most {MAX_DESCRIPTION_CHARS} characters")

    vis = manifest.get("visibility")
    if vis is not None and vis not in ("public", "private"):
        fail('visibility must be "public" or "private" when present')

    tags = manifest.get("tags", [])
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        fail(f"tags must be an array of at most {MAX_TAGS} strings")
    else:
        for i, t in enumerate(tags):
            if not isinstance(t, str) or not t.strip() or len(t.strip()) > MAX_TAG_CHARS:
                fail(f"tag {i + 1} must be a non-empty string of at most {MAX_TAG_CHARS} characters", f"tags[{i}]")

    skills = manifest.get("skills", [])
    if not isinstance(skills, list) or len(skills) > MAX_SKILLS:
        fail(f"skills must be an array of at most {MAX_SKILLS} entries")
    else:
        seen: set[str] = set()
        for i, s in enumerate(skills):
            entry = f"skills[{i}]"
            if not isinstance(s, dict):
                fail("skill entry must be an object", entry)
                continue
            sn = s.get("name")
            if not isinstance(sn, str) or not NAME_RE.match(sn):
                fail("skill name must match [A-Za-z0-9._-], 1-64 chars", entry)
            elif sn in seen:
                fail(f"duplicate skill name '{sn}'", entry)
            else:
                seen.add(sn)
            sd = s.get("description")
            if not isinstance(sd, str) or not sd.strip() or len(sd) > MAX_SKILL_DESC_CHARS:
                fail(f"skill description must be a non-empty string of at most {MAX_SKILL_DESC_CHARS} characters", entry)
            # add-skill-supply-annotations: optional zh base display name. Declaring it
            # obliges every i18n locale block to carry skills['<name>'].name.
            st = s.get("title")
            if "title" in s and (not isinstance(st, str) or not st.strip() or len(st) > MAX_SKILL_TITLE_CHARS):
                fail(f"skill title must be a non-empty string of at most {MAX_SKILL_TITLE_CHARS} characters", f"{entry}.title")
            sc = s.get("content")
            if not isinstance(sc, str) or not sc.strip() or len(sc) > MAX_SKILL_BODY:
                fail(f"skill content must be a non-empty string of at most {MAX_SKILL_BODY} characters", entry)
            # Skill package (add-packaged-skills): optional inline files map + frontmatter.
            aux_chars = 0
            fm = s.get("frontmatter")
            if "frontmatter" in s:
                if not isinstance(fm, dict):
                    fail("skill frontmatter must be an object of YAML mapping fields", f"{entry}.frontmatter")
                else:
                    aux_chars += len(json.dumps(fm))
            files = s.get("files")
            if "files" in s:
                if not isinstance(files, dict):
                    fail("skill files must be an object mapping relative paths to text content", f"{entry}.files")
                else:
                    if len(files) > MAX_SKILL_AUX_FILES:
                        fail(f"skill files must contain at most {MAX_SKILL_AUX_FILES} entries (found {len(files)})", entry)
                    folded = set()
                    for raw_path, text in files.items():
                        fentry = f"{entry}.files['{raw_path}']"
                        if not isinstance(text, str):
                            fail("file content must be text (a string)", fentry)
                            continue
                        bad = (not raw_path or "\0" in raw_path or "\\" in raw_path
                               or re.match(r"^[A-Za-z]:", raw_path) or raw_path.startswith("/"))
                        parts = [p for p in raw_path.split("/") if p not in ("", ".")]
                        if bad or not parts or ".." in parts:
                            fail("file path must be relative and safe (no absolute paths, '..', backslashes, drive letters, or NUL)", fentry)
                            continue
                        safe = "/".join(parts)
                        if re.search(r"(^|/)skill\.md$", safe, re.I):
                            fail("files may not include a SKILL.md (any casing)", fentry)
                            continue
                        base = safe.rsplit("/", 1)[-1]
                        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
                        if base not in TEXT_FILENAMES and ext not in TEXT_EXTENSIONS:
                            fail("file is not a text type the platform stores", fentry)
                            continue
                        low = safe.lower()
                        if low in folded:
                            fail(f"file path '{safe}' collides (case-insensitively) with another file in this skill", fentry)
                            continue
                        folded.add(low)
                        if len(text) > MAX_SKILL_FILE_CHARS:
                            fail(f"file '{safe}' exceeds the per-file limit of {MAX_SKILL_FILE_CHARS} characters", fentry)
                            continue
                        aux_chars += len(text)
                    if aux_chars > MAX_SKILL_AUX_CHARS:
                        fail(f"skill '{s.get('name', i)}' auxiliary content (files + frontmatter) exceeds {MAX_SKILL_AUX_CHARS} characters in total", entry)

    mcp = manifest.get("mcpServers", [])
    if not isinstance(mcp, list) or len(mcp) > MAX_MCP_REFS:
        fail(f"mcpServers must be an array of at most {MAX_MCP_REFS} entries")
    else:
        for i, m in enumerate(mcp):
            entry = f"mcpServers[{i}]"
            if not isinstance(m, dict):
                fail("MCP entry must be an object", entry)
                continue
            forbidden = [k for k in MCP_FORBIDDEN_KEYS if k in m]
            if forbidden:
                fail("MCP entries may only reference registry servers "
                     f"(found forbidden key(s): {', '.join(forbidden)})", entry)
                continue
            rn = m.get("registryName")
            if not isinstance(rn, str) or not NAME_RE.match(rn):
                fail("MCP entry needs a registryName matching [A-Za-z0-9._-], 1-64 chars", entry)
            rg = m.get("requiredGroup")
            if rg is not None and (not isinstance(rg, str) or not rg.strip() or len(rg) > 64):
                fail("MCP entry requiredGroup must be a non-empty string of at most 64 characters", entry)

    agents = manifest.get("agents", [])
    if not isinstance(agents, list) or len(agents) > MAX_AGENTS:
        fail(f"agents must be an array of at most {MAX_AGENTS} entries")
    else:
        seen_ids: set[str] = set()
        for i, a in enumerate(agents):
            entry = f"agents[{i}]"
            if not isinstance(a, dict):
                fail("agent entry must be an object", entry)
                continue
            forbidden = [k for k in AGENT_FORBIDDEN_KEYS if k in a]
            if forbidden:
                fail("agent entries are persona-only "
                     f"(found forbidden key(s): {', '.join(forbidden)})", entry)
                continue
            aid = a.get("id")
            if not isinstance(aid, str) or not NAME_RE.match(aid):
                fail("agent id must match [A-Za-z0-9._-], 1-64 chars", entry)
            elif aid.startswith("user."):
                fail(f"agent id '{aid}' falls under the reserved \"user.\" namespace (custom presets)", entry)
            elif aid in seen_ids:
                fail(f"duplicate agent id '{aid}'", entry)
            else:
                seen_ids.add(aid)
            an = a.get("name")
            if not isinstance(an, str) or not an.strip() or len(an) > MAX_AGENT_NAME_CHARS:
                fail(f"agent name must be a non-empty string of at most {MAX_AGENT_NAME_CHARS} characters", entry)
            ap = a.get("persona")
            if not isinstance(ap, str) or not ap.strip() or len(ap) > MAX_PERSONA_CHARS:
                fail(f"agent persona must be a non-empty string of at most {MAX_PERSONA_CHARS} characters", entry)

    # Locale overlay (add-pack-locale-tracks, 2026-10-27): zh stays the main
    # track in the base fields; the optional i18n block only covers them.
    if "i18n" in manifest:
        validate_locale_overlay(manifest, fail)

    part_count = (len(skills) if isinstance(skills, list) else 0) \
        + (len(mcp) if isinstance(mcp, list) else 0) \
        + (len(agents) if isinstance(agents, list) else 0)
    if part_count == 0:
        fail("a pack must contain at least one skill, MCP reference, or agent")

    return errors


def verdict_line(name: str, stats: str, degraded_reason: str | None) -> str:
    """结论行：形状通过 ≠ 判据跑过。读面拿不到时**不许**给裸 ✓。

    规则出处 = paas/DEPLOY.md「平台库诊断纪律：先判活，再下结论」
    （retire-orphan-platform-state 4.1/4.3）：读不到活来源时必须报错或标
    `degraded=true`，退回快照/跳过之后仍输出确定结论是不允许的。
    这里「形状」是本地能判的，所以不报错；但结论必须自报降级，消费方不得把
    裸 rc=0 读成「全部有效」。
    """
    if degraded_reason:
        return f"⚠ {name} ({stats}) — degraded=true: {degraded_reason}"
    return f"✓ {name} ({stats})"


# ── 人口枚举（--all）──────────────────────────────────────────────────────────
# 排除的不是「不想看的」，而是**同一份内容的副本或生成物**：`_parts/` 是拆分件、
# `dist/` 是打包产物、`__pycache__`/`node_modules` 不是清单。把这些算进人口会让
# 份数虚高，而份数以「要送发布闸的 manifest」为准。
EXCLUDED_DIRS = {"_parts", "dist", "__pycache__", "node_modules", ".git"}


def discover_manifests(root: Path) -> list[Path]:
    """`root` 下所有 pack manifest（`manifest.json`），跳过 `EXCLUDED_DIRS` 与点目录。"""
    out: list[Path] = []
    for p in root.rglob("manifest.json"):
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return sorted(out)


def discover_unraced_casts(root: Path) -> list[Path]:
    """`cast.json` 是 pack 形状（带 `mcpServers[].allowTools` / `fleet.i18n`），但它**不是**
    manifest，所以上面那份人口枚举永远收不到它。

    同目录有 `manifest.json` 时它属于那个已进人口的 pack（判定在 manifest 上做）；
    **没有**时它就是一份人口之外的声明——里面写的 allowTools 一条都不会被核。
    这里只把这类文件**点出来**（事实行、不参与判定）：它可能只是模板/工作稿，
    也可能是「写了却永远没人验」的声明，两种形态在输出上分不开，所以不许静默。
    """
    out: list[Path] = []
    for p in root.rglob("cast.json"):
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if (p.parent / "manifest.json").exists():
            continue
        out.append(p)
    return sorted(out)


# 自证臂数：写在常量里由 `self_test()` 自核，防「删了臂而汇总照样绿」。
SELF_TEST_ARMS = 12


def self_test() -> int:
    """离线自证（不碰 registry、不联网）：`--all` 的人口、汇总与降级语义。

    每一臂都断言**判决**（退出码或汇总行里的具体数字），不只断言「有输出」——
    只判「跑完了」的话，把汇总行整段删掉也会绿。
    """
    import contextlib
    import io
    import tempfile

    arms = 0
    fails: list[str] = []

    def arm(name: str, ok: bool, detail: str = "") -> None:
        nonlocal arms
        arms += 1
        if not ok:
            fails.append(f"{name}{(' — ' + detail) if detail else ''}")

    def run(argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = main(argv)
        return rc, buf.getvalue()

    def write_pack(root: Path, rel: str, mcp: list[dict]) -> Path:
        p = root / rel / "manifest.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"name": rel.replace("/", "-"), "title": "T", "mcpServers": mcp}),
            encoding="utf-8",
        )
        return p

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 健康包：一个 mcp 引用、无 allowTools ⇒ 不报错也不降级
        write_pack(root, "ok-pack", [{"registryName": "fd-text-core"}])
        # 有 allowTools 但读面不可用（--no-live）⇒ 降级 b>=1
        write_pack(root, "degraded-pack", [{"registryName": "fd-open-data-mcp", "allowTools": ["read"]}])

        rc, out = run(["v.py", "--all", "--all-root", td, "--no-live", "--mirror"])
        arm("A 读面不可用 ⇒ 退出码 0（降级不算失败）", rc == 0, f"rc={rc}")
        arm("B 汇总行含份数/有错/降级三个数", "汇总：2 份 / 有错 0 / 降级 1" in out, out[-160:])
        arm(
            "C 降级时点名「没验过任何 allowTools」，且没有任何「已核」字样",
            "没有**验过任何 allowTools 声明" in out and "✓ allowTools 已核" not in out,
            out[-160:],
        )

        # 排除目录：`_parts/` 下的坏清单不进人口（份数不动、也不会被判错）
        parts = root / "_parts" / "x" / "manifest.json"
        parts.parent.mkdir(parents=True, exist_ok=True)
        parts.write_text(json.dumps({"name": "bad", "mcpServers": "not-a-list"}), encoding="utf-8")
        rc2, out2 = run(["v.py", "--all", "--all-root", td, "--no-live", "--mirror"])
        arm("D `_parts/` 不计入人口", "汇总：2 份 / 有错 0" in out2 and rc2 == 0, out2[-160:])

        # 有错：population 里放一份真坏的（mcpServers 类型错）
        bp = write_pack(root, "bad-pack", [])
        bp.write_text(json.dumps({"name": "bad", "mcpServers": "not-a-list"}), encoding="utf-8")
        rc3, out3 = run(["v.py", "--all", "--all-root", td, "--no-live", "--mirror"])
        arm("E 有错 ⇒ 退出码 1 且汇总 a>=1", rc3 == 1 and "有错 1" in out3, f"rc={rc3} {out3[-160:]}")

        # 空人口 ⇒ 判不出（退 2），不是通过
        empty = root / "empty"
        empty.mkdir()
        rc4, out4 = run(["v.py", "--all", "--all-root", str(empty), "--no-live", "--mirror"])
        arm("F 人口为空 ⇒ 退出码 2 并明说判不出", rc4 == 2 and "判不出" in out4, f"rc={rc4}")

        # ── 降级判词：读面拿不到时不许给裸 ✓（degraded=true + DEGRADED 尾行）
        v_ok = verdict_line("m.json", "skills 1 / mcp 1 / agents 1", None)
        arm("G 非降级给 ✓", v_ok.startswith("✓ ") and "degraded" not in v_ok, v_ok)
        v_dg = verdict_line("m.json", "skills 1 / mcp 1 / agents 1", "allowTools 未校验")
        arm("H 降级不给裸 ✓", not v_dg.startswith("✓"), v_dg)
        arm("I 降级带 degraded=true", "degraded=true" in v_dg, v_dg)
        arm("J 降级念出原因", "allowTools 未校验" in v_dg, v_dg)
        # K 端到端：helper 写对、主流程没接上是另一种形态，只测纯函数盖不住。
        dpath = write_pack(root, "deg-pack", [{"registryName": "fd-open-data-mcp", "allowTools": ["read"]}])
        rc5, out5 = run(["v.py", "--no-live", "--mirror", str(dpath)])
        arm(
            "K 单文件 --no-live ⇒ degraded=true + DEGRADED 尾行，且不出裸 ✓",
            rc5 == 0 and "degraded=true" in out5 and "DEGRADED" in out5 and "✓ manifest.json" not in out5,
            f"rc={rc5} {out5[-200:]}",
        )

        # 人口外声明：`cast.json` 没有同目录 manifest ⇒ 点名、但**不改退出码**
        # （它必须与「份数」分开：混进人口会让份数虚高，静默掉则是一份声明永远没人验）
        # 用独立夹具根：上面几臂已经在 root 里放了一份坏包，混进来 rc 会由它决定。
        gr = root / "cast-root"
        gr.mkdir()
        stray = gr / "cast-only" / "cast.json"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text(
            json.dumps({"name": "cast-only", "mcpServers": [{"registryName": "fd-text-core"}]}),
            encoding="utf-8",
        )
        # 同目录既有 manifest 又有 cast 的，属于已进人口的那个 pack，不该被点名
        write_pack(gr, "both-pack", [{"registryName": "fd-text-core"}])
        (gr / "both-pack" / "cast.json").write_text("{}", encoding="utf-8")
        rc6, out6 = run(["v.py", "--all", "--all-root", str(gr), "--no-live", "--mirror"])
        arm(
            "L 人口外 cast.json 被点名且不改退出码、不进份数",
            rc6 == 0
            and "不在人口里" in out6
            and "cast-only/cast.json" in out6
            and "both-pack/cast.json" not in out6
            and "汇总：1 份" in out6,
            f"rc={rc6} {out6[-200:]}",
        )

    if arms != SELF_TEST_ARMS:
        print(f"✗ 自证臂数不符：跑了 {arms}，常量写 {SELF_TEST_ARMS}")
        return 1
    if fails:
        print(f"✗ self-test {len(fails)}/{arms} 臂失败：")
        for f in fails:
            print(f"    - {f}")
        return 1
    print(f"✓ self-test {arms}/{arms} 臂通过（--all 人口/汇总/降级/排除/判不出/人口外声明）")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if "--self-test" in argv:
        return self_test()
    explicit_paas = None
    if "--paas" in argv:
        idx = argv.index("--paas")
        if idx + 1 >= len(argv):
            print("--paas needs a path", file=sys.stderr)
            return 2
        explicit_paas = argv[idx + 1]
        args = [a for a in args if a != explicit_paas]
    if "--mirror" in argv:
        explicit_paas = "/nonexistent"  # force the mirrored rule set

    all_root: Path | None = None
    if "--all-root" in argv:
        idx = argv.index("--all-root")
        if idx + 1 >= len(argv):
            print("--all-root needs a path", file=sys.stderr)
            return 2
        all_root = Path(argv[idx + 1])
        args = [a for a in args if a != argv[idx + 1]]

    required_locales: list[str] = []
    if "--require-locales" in argv:
        idx = argv.index("--require-locales")
        if idx + 1 >= len(argv):
            print("--require-locales needs a comma-separated list (e.g. en,ja,es,fr)", file=sys.stderr)
            return 2
        raw = argv[idx + 1]
        required_locales = [x.strip().lower() for x in raw.split(",") if x.strip()]
        args = [a for a in args if a != raw]
        bad = [
            tag for tag in required_locales
            if not LOCALE_KEY_RE.match(tag) or tag == "zh" or tag.startswith("zh-")
        ]
        if not required_locales or bad:
            print(
                f"--require-locales has invalid/zh tag(s): {', '.join(bad) or '(empty)'}",
                file=sys.stderr,
            )
            return 2

    if not args and "--all" not in argv:
        print(__doc__)
        return 2

    paas = find_paas(explicit_paas)
    mode = f"real validator ({paas}/lib/pack-manifest.js)" if paas else "mirrored rules (paas not found)"
    print(f"validator: {mode}\n")

    # ── 人口显式化（--all）────────────────────────────────────────────────────
    # 此前这个闸只校验「被点名传进来的那几份」：谁没被点名，谁就永远绿。这正是
    # 「闸的人口被静默收窄」那一族——2026-11-17 audit 时全仓 33 份 manifest 里
    # 有一批从没进过任何一次校验。--all 把人口的**枚举**写进闸里，并把份数打出来
    # （读闸的人要能看见分母）。
    all_mode = "--all" in argv
    if all_mode:
        pop_root = all_root or Path(__file__).resolve().parents[1]
        if not pop_root.is_dir():
            print(f"✗ --all: 人口根不存在：{pop_root}", file=sys.stderr)
            return 2
        args = [str(p) for p in discover_manifests(pop_root)]
        print(
            f"population: {len(args)} 份 manifest.json（root={pop_root}；"
            f"排除目录 {', '.join(sorted(EXCLUDED_DIRS))}）\n"
        )
        if not args:
            print(f"✗ --all: 在 {pop_root} 下一份 manifest.json 都没扫到 —— 判不出，不是通过")
            return 2

    # allowTools 实探闸：一次取数，全批复用（kubectl exec 不便宜）。
    surfaces = None
    if "--no-live" in argv:
        print("! allowTools 未校验：--no-live\n")
    else:
        surfaces = live_tool_surfaces()
        print(f"tool surface: {TOOL_SOURCE_NOTE or 'unavailable'}\n")
    strict_allow = "--strict-allowtools" in argv

    any_bad = False
    files_with_errors = 0
    files_degraded = 0
    degraded_any = False

    def label(path: Path) -> str:
        """`--all` 下必须打**相对路径**：38 份都叫 `manifest.json`，只打名字等于
        判词不印当事的事实值——读的人无法知道是哪一份坏了。"""
        if all_mode:
            try:
                return str(path.relative_to(pop_root))
            except ValueError:
                return str(path)
        return path.name

    for path_str in args:
        path = Path(path_str)
        if not path.exists():
            print(f"✗ {path}: not found")
            any_bad = True
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"✗ {label(path)}: invalid JSON — {e}")
            any_bad = True
            continue

        try:
            errors = validate_with_real(paas, path) if paas else validate_mirrored(manifest)
        except Exception as e:
            print(f"! {label(path)}: real validator unavailable ({e}); using mirrored rules")
            errors = validate_mirrored(manifest)

        # Local-only gates: fleet.i18n is invisible to the paas publish validator,
        # and the release coverage mode is a finddata-side bar.
        file_degraded = False
        if isinstance(manifest, dict):
            extra_errors: list[dict] = []

            def extra_fail(msg: str, entry: str | None = None) -> None:
                extra_errors.append({"error": msg, **({"entry": entry} if entry else {})})

            validate_fleet_i18n(manifest, extra_fail)
            if required_locales:
                extra_errors.extend(validate_locale_coverage(manifest, required_locales))

            warn_lines: list[str] = []
            unverified: list[str] = []
            checked = validate_allow_tools(
                manifest,
                surfaces,
                extra_fail,
                lambda msg, entry=None: warn_lines.append(
                    f"{msg}{f' [{entry}]' if entry else ''}"
                ),
                unverified,
            )
            if strict_allow and surfaces is None:
                extra_fail(f"allowTools 未能校验：{TOOL_SOURCE_NOTE or '读面不可用'}")
            if unverified:
                files_degraded += 1
                # 「这一份有 allowTools 声明、而读面拿不到」⇒ 它的判词必须是
                # degraded，不是 ✓。计数与判词是两个面，缺了判词只有汇总里的
                # 一个数，读单份的人仍会把 ⚠ 当通过。
                file_degraded = surfaces is None
            errors = list(errors) + extra_errors
            for line in warn_lines:
                print(f"  ! {line}")
            if checked:
                print(f"  ✓ allowTools 已核（{TOOL_SOURCE_NOTE}）：{', '.join(sorted(set(checked)))}")

        skills = manifest.get("skills", []) if isinstance(manifest, dict) else []
        mcp = manifest.get("mcpServers", []) if isinstance(manifest, dict) else []
        agents = manifest.get("agents", []) if isinstance(manifest, dict) else []
        stats = f"skills {len(skills)} / mcp {len(mcp)} / agents {len(agents)}"

        if errors:
            any_bad = True
            files_with_errors += 1
            print(f"✗ {label(path)} ({stats}) — {len(errors)} error(s):")
            for err in errors:
                entry = f" [{err.get('entry')}]" if err.get("entry") else ""
                print(f"    - {err.get('error')}{entry}")
        else:
            reason = (
                f"allowTools 未校验（{TOOL_SOURCE_NOTE or '--no-live'}）"
                if file_degraded
                else None
            )
            print(verdict_line(label(path), stats, reason))
            if reason:
                degraded_any = True

    if all_mode:
        # 三个数缺一不可：少了「份数」时分母不可见；少了「降级」时
        # 「读面取不到 ⇒ allowTools 一个都没核」会被读成「全部通过」。
        print(
            f"\n汇总：{len(args)} 份 / 有错 {files_with_errors} / 降级 {files_degraded}"
        )
        if files_degraded and not files_with_errors and surfaces is None:
            print(
                f"  ! 有 {files_degraded} 份的 allowTools 因读面不可用未核 —— "
                "这一轮**没有**验过任何 allowTools 声明（不是「都合规」）"
            )
        # 人口外声明：`cast.json` 是 pack 形状但不叫 manifest ⇒ 上面的人口枚举看不见它。
        # 只报事实（不改退出码）：它可能是模板/工作稿，也可能是永远没人验的一份声明。
        unraced = discover_unraced_casts(pop_root)
        if unraced:
            print(
                f"  · 另有 {len(unraced)} 个 cast.json 没有同目录 manifest.json ⇒ **不在人口里**"
                "（不参与判定，需人判是工作稿还是漏登记的 pack）："
                + "、".join(label(p) for p in unraced)
            )

    if degraded_any:
        print(
            "\nDEGRADED: 本轮有 allowTools 声明未校验 —— 形状合法，但不得据此判「全部有效」"
            + ("" if strict_allow else "（加 --strict-allowtools 可把这一档升级为失败）")
        )

    return 1 if any_bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
