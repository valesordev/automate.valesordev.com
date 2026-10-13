#!/usr/bin/env python3
"""Check the authoring conventions in CLAUDE.md that `claude plugin validate` doesn't.

    check_conventions.py [--root DIR]

DIR defaults to the git toplevel of the working directory.

- every skill ships references/examples.md
- every skill's frontmatter fits claude.ai's skill upload: `name` is the
  directory name, kebab-case, at most 64 characters, without "anthropic" or
  "claude"; `description` is non-empty, at most 1024 characters, without `<`/`>`
- every agent, base role, and composed target role declares
  `Assist level: L<n>` before its first `## ` section
- every agent and base role has a row in its plugin README's roles table,
  with the same assist level (the first L<n> in each)

Base roles named by `extends` may come from a dependency (deps.json).

Exits 1 and lists every problem if any are found.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install  # noqa: E402
from sources import Sources, repo_root  # noqa: E402

ASSIST = re.compile(r"^Assist level: L([0-3])\b", re.M)
LEVEL = re.compile(r"\bL([0-3])\b")


def body(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def declared_level(text: str) -> str | None:
    """The assist level declared before the first `## ` section, if any."""
    pre = re.split(r"^## ", body(text), maxsplit=1, flags=re.M)[0]
    m = ASSIST.search(pre)
    return m.group(1) if m else None


def readme_levels(readme: Path) -> dict[str, str]:
    """`name` -> level from table rows whose first cell starts with a backticked name."""
    out: dict[str, str] = {}
    if not readme.is_file():
        return out
    for line in readme.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.match(r"^`([a-z0-9-]+)`", cells[0])
        lv = LEVEL.search(cells[1])
        if m and lv:
            out.setdefault(m.group(1), lv.group(1))
    return out


def frontmatter(text: str) -> dict[str, str] | None:
    """Top-level `key: value` pairs, with folded continuation lines joined."""
    if not text.startswith("---\n") or "\n---\n" not in text[3:]:
        return None
    out, key = {}, None
    for line in text[4:text.index("\n---\n", 3)].splitlines():
        m = re.match(r"^([a-z][a-z-]*):\s?(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2)
        elif key:
            out[key] += " " + line.strip()
    return out


def skill_problems(skill: Path, rel) -> list[str]:
    meta = frontmatter(skill.read_text())
    where = rel(skill)
    if meta is None:
        return [f"{where}: no frontmatter"]
    name, desc = meta.get("name", "").strip(), meta.get("description", "").strip()
    out = []
    if name != skill.parent.name:
        out.append(f"{where}: name {name!r} isn't the directory name")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64 or re.search(r"anthropic|claude", name):
        out.append(f"{where}: name {name!r} won't upload (kebab-case, at most 64, no 'anthropic'/'claude')")
    if not desc or len(desc) > 1024:
        out.append(f"{where}: description is empty or over 1024 characters")
    if re.search(r"[<>]", desc):
        out.append(f"{where}: description has < or >, which claude.ai rejects")
    return out


def check(root: Path) -> list[str]:
    problems: list[str] = []
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731

    for skill in sorted(root.glob("plugins/*/skills/*/SKILL.md")):
        if not (skill.parent / "references" / "examples.md").is_file():
            problems.append(f"{rel(skill.parent)}: missing references/examples.md")
        problems += skill_problems(skill, rel)

    for pdir in sorted(p for p in (root / "plugins").glob("*") if p.is_dir()):
        table = readme_levels(pdir / "README.md")
        for kind in ("agents", "roles"):
            for f in sorted((pdir / kind).glob("*.md")):
                level = declared_level(f.read_text())
                if level is None:
                    problems.append(f"{rel(f)}: no 'Assist level: L<n>' before the first ## section")
                    continue
                name = f.stem
                if name not in table:
                    problems.append(f"{rel(pdir / 'README.md')}: no roles-table row for `{name}` ({kind})")
                elif table[name] != level:
                    problems.append(f"{rel(pdir / 'README.md')}: `{name}` is L{table[name]} in the "
                                    f"table but L{level} in {rel(f)}")

    sources = Sources(root)
    for tjson in sorted(root.glob("targets/*/target.json")):
        cfg = json.loads(tjson.read_text())
        rdir = tjson.parent / cfg.get("roles", "roles")
        for f in sorted(rdir.glob("*.md")) if rdir.is_dir() else []:
            if f.name.startswith("_"):
                continue
            composed = install.compose_role(f, cfg.get("plugins", []), sources).decode()
            if declared_level(composed) is None:
                problems.append(f"{rel(f)}: composed role has no 'Assist level: L<n>' before "
                                f"the first ## section (here or in its `extends` base)")
    return problems


def main(argv: list[str]) -> int:
    root = repo_root(list(argv))
    problems = check(root)
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} convention problem(s)")
        return 1
    print("conventions ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
