#!/usr/bin/env python3
"""Fetch the pinned external tools into .tools/, checksum-verified.

    tools.py [--root DIR]

Today that's gitleaks, for check_public.py. An existing .tools/gitleaks at the
pinned version is left alone.
"""
import argparse
import hashlib
import io
import platform
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

GITLEAKS = "8.30.1"
SHA256 = {
    "linux_x64": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    "linux_arm64": "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
    "darwin_x64": "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
    "darwin_arm64": "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
}


def plat() -> str:
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    key = f"{platform.system().lower()}_{arch}"
    if key not in SHA256:
        raise SystemExit(f"tools: no pinned gitleaks for {key}; install gitleaks {GITLEAKS} and set GITLEAKS")
    return key


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd())
    exe = ap.parse_args().root.resolve() / ".tools/gitleaks"
    if exe.exists():
        have = subprocess.run([str(exe), "version"], capture_output=True, text=True).stdout.strip()
        if have.lstrip("v") == GITLEAKS:
            return 0
    key = plat()
    url = (f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS}/"
           f"gitleaks_{GITLEAKS}_{key}.tar.gz")
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != SHA256[key]:
        raise SystemExit(f"tools: checksum mismatch for {url}")
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.extractfile("gitleaks")
        exe.parent.mkdir(exist_ok=True)
        exe.write_bytes(member.read())
    exe.chmod(0o755)
    print(f"  .tools/gitleaks {GITLEAKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
