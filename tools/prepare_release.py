"""Create a versioned release ZIP and updater manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.version import APP_EXE_NAME, APP_VERSION, UPDATE_MANIFEST_URL  # noqa: E402

VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2,}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid version: {version!r}")
    return version


def prepare_release(exe: Path, output_dir: Path, notes: str = "", force: bool = False) -> tuple[Path, Path]:
    version = validate_version(APP_VERSION)
    if not exe.is_file():
        raise FileNotFoundError(exe)
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = f"autobet-pro-{version}"
    zip_path = output_dir / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(exe, APP_EXE_NAME)
    base_url = UPDATE_MANIFEST_URL.rsplit("/", 1)[0]
    manifest = {
        "version": version,
        "url": f"{base_url}/{zip_path.name}",
        "sha256": sha256_file(zip_path),
        "notes": notes,
        "force": force,
    }
    manifest_path = output_dir / "latest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return zip_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=BACKEND / "dist" / APP_EXE_NAME)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release_upload")
    parser.add_argument("--notes", default="")
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    notes = args.notes_file.read_text(encoding="utf-8").strip() if args.notes_file else args.notes
    zip_path, manifest_path = prepare_release(args.exe, args.output_dir, notes, args.force)
    print(zip_path)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
