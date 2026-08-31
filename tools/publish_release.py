"""Publish prepared updater files to Cloudflare R2 using Wrangler."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release(manifest_path: Path, allowed_host: str) -> tuple[dict, Path, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    url = urllib.parse.urlparse(str(manifest.get("url", "")))
    if url.scheme != "https" or (url.hostname or "").lower() != allowed_host.lower():
        raise ValueError("manifest download host is not allowed")
    if not url.path.startswith("/releases/") or not url.path.endswith(".zip"):
        raise ValueError("manifest URL must point to a ZIP under /releases/")
    zip_path = manifest_path.parent / Path(url.path).name
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    expected = str(manifest.get("sha256", "")).lower()
    if len(expected) != 64 or sha256_file(zip_path) != expected:
        raise ValueError("release ZIP SHA-256 does not match manifest")
    return manifest, zip_path, url.path.lstrip("/")


def publish_release(manifest_path: Path, bucket: str, allowed_host: str, confirm: str, runner=subprocess.run) -> None:
    if confirm != "PUBLISH":
        raise ValueError("publishing requires --confirm PUBLISH")
    _, zip_path, object_key = validate_release(manifest_path, allowed_host)
    commands = [
        ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{object_key}", "--file", str(zip_path), "--remote"],
        ["npx", "wrangler", "r2", "object", "put", f"{bucket}/releases/latest.json", "--file", str(manifest_path), "--content-type", "application/json", "--remote"],
    ]
    for command in commands:
        runner(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "release_upload" / "latest.json")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--allowed-host", default="pub-465f078b4f484662b30eb39d27ae5155.r2.dev")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    publish_release(args.manifest, args.bucket, args.allowed_host, args.confirm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
