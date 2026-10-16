#!/usr/bin/env python3
"""Re-read the container inventory from GHCR and report any drift.

Every entry in resources/images.json names an immutable GHCR reference. This reads each
manifest and its config blob and checks the recorded digests against what the registry
actually serves. Standard library only, so it runs anywhere the validator runs.

    python scripts/verify_images.py
    python scripts/verify_images.py --update

Reads need package access while the packages are private. Set GH_TOKEN (or GITHUB_TOKEN)
and GHCR_USER to a token and account that can read them; anonymous pulls work once the
packages are public. The script never writes unless --update is passed.

Scope: manifest and config metadata only, matching the recorded verification. It does not
download layer payloads or run containers.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "resources" / "images.json"

REGISTRY = "ghcr.io"
PLATFORM = "linux/amd64"

MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
IMAGE_TYPES = ("application/vnd.oci.image.manifest.v1+json",
               "application/vnd.docker.distribution.manifest.v2+json")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")


def fetch(url: str, headers: dict[str, str], attempts: int = 3):
    """GET a registry URL, returning the body and its (case-insensitive) headers.

    Blob reads redirect to a CDN whose resolution can fail transiently, so retry before
    giving up. The header object is returned as-is rather than copied into a dict, which
    would make Docker-Content-Digest case-sensitive.
    """
    last = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"GET {url} -> {error.code} {error.reason}") from error
        except urllib.error.URLError as error:
            last = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} -> {last.reason}") from last


def pull_token(repository: str) -> str:
    url = f"https://{REGISTRY}/token?service={REGISTRY}&scope=repository:{repository}:pull"
    headers = {"Accept": "application/json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    # GHCR wants the GitHub account name beside the token, not a fixed placeholder.
    user = os.environ.get("GHCR_USER") or os.environ.get("GITHUB_ACTOR")
    if token and user:
        raw = base64.b64encode(f"{user}:{token}".encode()).decode()
        headers["Authorization"] = f"Basic {raw}"
    body, _ = fetch(url, headers)
    value = json.loads(body).get("token")
    if not value:
        raise RuntimeError(f"no pull token for {repository}; set GH_TOKEN and GHCR_USER")
    return value


def read_image(reference: str) -> dict:
    """Resolve one GHCR reference to its manifest digest, config digest, and shape."""
    registry, remainder = reference.split("/", 1)
    if registry != REGISTRY:
        raise RuntimeError(f"noncanonical registry in {reference}")
    repository, _, name = remainder.rpartition(":")

    headers = {"Authorization": f"Bearer {pull_token(repository)}", "Accept": MANIFEST_TYPES}
    base = f"https://{REGISTRY}/v2/{repository}"

    body, response = fetch(f"{base}/manifests/{name}", headers)
    digest = response.get("Docker-Content-Digest")
    media = (response.get("Content-Type") or "").split(";")[0].strip()
    manifest = json.loads(body)

    if media in INDEX_TYPES:
        wanted_os, wanted_arch = PLATFORM.split("/")
        children = [m for m in manifest.get("manifests", [])
                    if m.get("platform", {}).get("os") == wanted_os
                    and m.get("platform", {}).get("architecture") == wanted_arch]
        if not children:
            raise RuntimeError(f"no {PLATFORM} manifest under {reference}")
        body, response = fetch(f"{base}/manifests/{children[0]['digest']}", headers)
        digest = response.get("Docker-Content-Digest") or children[0]["digest"]
        media = (response.get("Content-Type") or "").split(";")[0].strip()
        manifest = json.loads(body)

    if media not in IMAGE_TYPES:
        raise RuntimeError(f"unexpected media type for {reference}: {media}")

    config_digest = manifest["config"]["digest"]
    config_body, _ = fetch(f"{base}/blobs/{config_digest}", headers)
    config = json.loads(config_body)

    return {
        "canonical_manifest_digest": digest,
        "canonical_digest_reference": reference.rsplit(":", 1)[0] + "@" + digest,
        "config_digest": config_digest,
        "rootfs_diff_id_count": len(config.get("rootfs", {}).get("diff_ids", [])),
        "platform": f"{config.get('os')}/{config.get('architecture')}",
    }


def verify(entry: dict) -> list[str]:
    """Return the fields whose recorded value differs from what GHCR serves."""
    observed = read_image(entry["canonical"])

    differences = [f"{key}: recorded {entry.get(key)!r}, served {value!r}"
                   for key, value in observed.items() if entry.get(key) != value]

    # An archived local build additionally asserts that the transferred image still carries
    # the digest of the image it was built from.
    image_id = (entry.get("source_provenance") or {}).get("image_id")
    if image_id and observed["config_digest"] != image_id:
        differences.append(
            f"config digest no longer equals the source image id {image_id}")

    entry["_observed"] = observed
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="write the observed metadata back into the inventory")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())
    drifted, failed = [], []

    for entry in inventory["images"]:
        label = entry["canonical"]
        try:
            differences = verify(entry)
        except RuntimeError as error:
            failed.append(label)
            print(f"UNREADABLE  {label}\n            {error}", file=sys.stderr)
            continue
        if differences:
            drifted.append(label)
            print(f"DRIFT       {label}")
            for line in differences:
                print(f"            {line}")
        else:
            print(f"ok          {label}")

    if args.update and not failed:
        for entry in inventory["images"]:
            entry.update(entry.pop("_observed"))
        args.inventory.write_text(json.dumps(inventory, indent=2) + "\n")
        print(f"\nupdated {args.inventory}")
    elif args.update:
        print("\nnot updating: some entries could not be read", file=sys.stderr)

    print(f"\n{len(inventory['images'])} entries, {len(drifted)} drifted, {len(failed)} unreadable")
    return 1 if (drifted or failed) and not args.update else 0


if __name__ == "__main__":
    raise SystemExit(main())
