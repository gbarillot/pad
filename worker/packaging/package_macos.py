from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


WORKER_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = WORKER_ROOT / "dist" / "pad-worker"
TOOLS_ROOT = DIST_ROOT / "tools"
BIN_DIR = TOOLS_ROOT / "bin"
LIB_DIR = TOOLS_ROOT / "lib"
TESSDATA_DIR = TOOLS_ROOT / "share" / "tessdata"
HOMEBREW_PREFIX = Path(os.getenv("HOMEBREW_PREFIX", "/opt/homebrew"))


def main() -> None:
    build_worker()
    package_tools()
    patch_macos_library_paths()
    ad_hoc_sign()


def build_worker() -> None:
    shutil.rmtree(WORKER_ROOT / "build", ignore_errors=True)
    shutil.rmtree(DIST_ROOT, ignore_errors=True)
    command = [
        "uv",
        "run",
        "--with",
        "pyinstaller",
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "pad-worker",
        "--paths",
        str(WORKER_ROOT),
        str(WORKER_ROOT / "packaging" / "worker_main.py"),
    ]
    subprocess.run(command, cwd=WORKER_ROOT, check=True)


def package_tools() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    TESSDATA_DIR.mkdir(parents=True, exist_ok=True)

    for binary_name in ("tesseract", "pdftoppm", "pdfinfo"):
        binary_path = Path(shutil.which(binary_name) or "")
        if not binary_path.exists():
            raise FileNotFoundError(f"Required packaging binary not found on PATH: {binary_name}")
        copy_file(binary_path, BIN_DIR / binary_name)

    for traineddata in ("fra.traineddata", "eng.traineddata", "osd.traineddata"):
        source = find_file(HOMEBREW_PREFIX, traineddata)
        if not source:
            raise FileNotFoundError(f"Required tessdata file not found under {HOMEBREW_PREFIX}: {traineddata}")
        copy_file(source, TESSDATA_DIR / traineddata)

    pending = list(BIN_DIR.iterdir())
    seen: set[Path] = set()
    while pending:
        target = pending.pop()
        if target in seen or not target.exists() or target.is_dir():
            continue
        seen.add(target)
        for dependency in binary_dependencies(target):
            copied = LIB_DIR / dependency.name
            if not copied.exists():
                copy_file(dependency, copied)
                pending.append(copied)


def patch_macos_library_paths() -> None:
    patch_targets = [path for path in BIN_DIR.iterdir() if path.is_file()]
    patch_targets.extend(path for path in LIB_DIR.iterdir() if path.is_file() and path.suffix == ".dylib")

    for target in patch_targets:
        for dependency_ref, dependency_path in linked_dependency_refs(target):
            replacement = f"@executable_path/../lib/{dependency_path.name}"
            subprocess.run(["install_name_tool", "-change", dependency_ref, replacement, str(target)], check=False)
        if target.suffix == ".dylib":
            subprocess.run(["install_name_tool", "-id", f"@executable_path/../lib/{target.name}", str(target)], check=False)


def ad_hoc_sign() -> None:
    sign_targets = [path for path in LIB_DIR.iterdir() if path.is_file()]
    sign_targets.extend(path for path in BIN_DIR.iterdir() if path.is_file())
    sign_targets.append(DIST_ROOT / "pad-worker")
    for target in sign_targets:
        if target.exists():
            subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=False)


def binary_dependencies(binary: Path) -> list[Path]:
    return [dependency_path for _, dependency_path in linked_dependency_refs(binary)]


def linked_dependency_refs(binary: Path) -> list[tuple[str, Path]]:
    result = subprocess.run(["otool", "-L", str(binary)], check=True, text=True, capture_output=True)
    dependencies: list[tuple[str, Path]] = []
    for line in result.stdout.splitlines()[1:]:
        candidate = line.strip().split(" ", 1)[0]
        if candidate.startswith(str(HOMEBREW_PREFIX)):
            path = Path(candidate)
            if path.exists():
                dependencies.append((candidate, path))
        elif candidate.startswith("@rpath/") or candidate.startswith("@loader_path/"):
            path = find_file(HOMEBREW_PREFIX, Path(candidate).name)
            if path:
                dependencies.append((candidate, path))
    return dependencies


def find_file(root: Path, name: str) -> Path | None:
    for path in root.rglob(name):
        if path.is_file():
            return path
    return None


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o755)


if __name__ == "__main__":
    main()
