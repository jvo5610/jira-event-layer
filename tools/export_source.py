"""Create a clean source distribution with an explicit allowlist, never Git history.

This prepares files, not a public repository or a license grant. Destination must
not exist. Secrets, private lab helpers, .env, dumps, caches and evidence are excluded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT_FILES = (".gitignore", ".dockerignore", ".env.example", "Dockerfile", "compose.yaml", "compose.dev.yaml",
              "pyproject.toml", "uv.lock", "README.md", "CONTRIBUTING.md", "SECURITY.md", "use-cases.json",
              ".github/workflows/ci.yml")
PUBLIC_TOOLS = ("local_config.py", "init_installation.py", "selftest.py", "smoke_container.py", "e2e_live.py", "export_source.py")
SUFFIXES = {".py", ".sql", ".yaml", ".json"}


def export(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("Destination already exists; refusing to overwrite")
    paths = [Path(name) for name in ROOT_FILES]
    for folder in ("app", "tests", "examples", "deploy"):
        paths += [p.relative_to(root) for p in (root / folder).rglob("*")
                  if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts]
    paths += [Path("tools") / name for name in PUBLIC_TOOLS]
    if (root / "LICENSE").is_file():
        paths.append(Path("LICENSE"))
    # Conservative signature check in addition to the allowlist; not an exhaustive secret audit.
    forbidden = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bATATT[A-Za-z0-9_-]{30,}")
    manifest = {}
    for relative in sorted(paths):
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Refusing nonlocal source: {relative}")
        content = source.read_bytes()
        if forbidden.search(content.decode("utf-8")):
            raise ValueError(f"Potential credential in {relative}; source export refused")
        manifest[str(relative)] = hashlib.sha256(content).hexdigest()
    destination.mkdir(parents=True, mode=0o755)
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    (destination / "SOURCE-MANIFEST.json").write_text(json.dumps({"files": manifest,
        "git_history_included": False, "license_selected": (root / "LICENSE").is_file()}, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    args = parser.parse_args()
    files = export(Path(__file__).resolve().parents[1], args.destination)
    print(f"Exported {len(files)} allowlisted source files. Nothing was published.")
