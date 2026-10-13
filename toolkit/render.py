#!/usr/bin/env python3
"""Inline {{include path}} directives (recursive, repo-root relative).

    render.py [--root DIR] <src> <dst>             render one file (instructions/* -> dist/)
    render.py [--root DIR] --tree <plugin-dir>     render <plugin>/src/** onto <plugin>/** in place
    render.py [--root DIR] --check <plugin-dir>    exit 1 if any <plugin>/src/** render differs from the committed file

An include path is looked up in DIR (default: the git toplevel of the working
directory), then in each dependency pinned in DIR/deps.json (see sources.py).

Plugins load in place (`make dev`, vendoring), so files that need shared
content (agents, role bases, skills) are authored under `src/` and their
rendered output is committed at the matching path outside `src/`. A rendered
file starts with a marker comment after its frontmatter; never hand-edit it.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources import Sources, repo_root  # noqa: E402

INC = re.compile(r"\{\{\s*include\s+([^\s}]+)\s*\}\}")
MARK = "<!-- GENERATED from {src} by `make build`; edit the source, not this file. -->"


def render(path: pathlib.Path, sources: Sources, stack=()) -> str:
    if path in stack:
        sys.exit(f"include cycle: {' -> '.join(map(str, stack + (path,)))}")
    text = path.read_text()
    return INC.sub(lambda m: render(sources.include(m.group(1)).resolve(), sources,
                                    stack + (path,)).rstrip("\n"), text)


def with_marker(text: str, src_rel: str) -> str:
    mark = MARK.format(src=src_rel)
    if text.startswith("---\n"):
        end = text.index("\n---\n", 4) + 5
        return text[:end] + mark + "\n" + text[end:]
    return mark + "\n" + text


def tree(plugin: pathlib.Path, sources: Sources) -> dict[pathlib.Path, str]:
    src = plugin / "src"
    out = {}
    if src.is_dir():
        for f in sorted(src.rglob("*.md")):
            rel = f.relative_to(src)
            out[plugin / rel] = with_marker(render(f.resolve(), sources),
                                            f.resolve().relative_to(sources.root).as_posix())
    return out


def main(argv: list[str]) -> int:
    argv = list(argv)
    sources = Sources(repo_root(argv))
    root = sources.root
    if argv and argv[0] in ("--tree", "--check"):
        plugin = pathlib.Path(argv[1]).resolve()
        stale = []
        for dst, text in tree(plugin, sources).items():
            if argv[0] == "--tree":
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text)
                print(f"  {dst.relative_to(root)}")
            elif not dst.is_file() or dst.read_text() != text:
                stale.append(dst.relative_to(root).as_posix())
        for s in stale:
            print(f"stale render: {s} (run `make build`)", file=sys.stderr)
        return 1 if stale else 0
    src, dst = map(pathlib.Path, argv[:2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render(src.resolve(), sources))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
