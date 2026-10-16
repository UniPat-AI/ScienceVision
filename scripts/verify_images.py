#!/usr/bin/env python3
"""Re-read the container inventory from the registries and report any drift.

Every entry in resources/images.json names a source image and its canonical GHCR copy.
This walks both registries, reads each manifest and its config blob, and checks the
recorded digests against what the registries actually serve. Standard library only, so it
runs anywhere the validator runs.

    python scripts/verify_images.py
    python scripts/verify_images.py --update

Reads need package access for private registries. Set GH_TOKEN (or GITHUB_TOKEN) to a
token with read:packages; anonymous pulls work once the packages are public. Docker Hub
reads are anonymous. The script never writes unless --update is passed.

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

GHCR = "ghcr.io"
DOCKER_HUB = "registry-1.docker.io"

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
PLATFORM = "linux/amd64"


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


def ghcr_token(repository: str) -> str:
    url = f"https://{GHCR}/token?service={GHCR}&scope=repository:{repository}:pull"
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
        raise RuntimeError(f"no pull token for {repository}; set GH_TOKEN for private packages")
    return value


def docker_hub_token(repository: str) -> str:
    url = (f"https://auth.docker.io/token?service=registry.docker.io"
           f"&scope=repository:{repository}:pull")
    body, _ = fetch(url, {"Accept": "application/json"})
    return json.loads(body)["token"]


def registry_image(registry: str, repository: str, reference: str) -> dict:
    """Resolve one reference to its image manifest, digest, and config blob."""
    if registry == GHCR:
        token = ghcr_token(repository)
    else:
        token = docker_hub_token(repository)
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}
    base = f"https://{registry}/v2/{repository}"

    body, response = fetch(f"{base}/manifests/{reference}", headers)
    digest = response.get("Docker-Content-Digest")
    media = (response.get("Content-Type") or "").split(";")[0].strip()
    manifest = json.loads(body)

    if media in INDEX_TYPES:
        children = [m for m in manifest.get("manifests", [])
                    if m.get("platform", {}).get("os") == PLATFORM.split("/")[0]
                    and m.get("platform", {}).get("architecture") == PLATFORM.split("/")[1]]
        if not children:
            raise RuntimeError(f"no {PLATFORM} manifest under {repository}:{reference}")
        body, response = fetch(f"{base}/manifests/{children[0]['digest']}", headers)
        digest = response.get("Docker-Content-Digest") or children[0]["digest"]
        media = (response.get("Content-Type") or "").split(";")[0].strip()
        manifest = json.loads(body)

    if media not in IMAGE_TYPES:
        raise RuntimeError(f"unexpected media type for {repository}:{reference}: {media}")

    config_digest = manifest["config"]["digest"]
    config_body, _ = fetch(f"{base}/blobs/{config_digest}", headers)
    config = json.loads(config_body)

    return {
        "manifest_digest": digest,
        "config_digest": config_digest,
        "layer_digests": [layer["digest"] for layer in manifest.get("layers", [])],
        "rootfs_diff_id_count": len(config.get("rootfs", {}).get("diff_ids", [])),
        "platform": f"{config.get('os')}/{config.get('architecture')}",
    }


def split(reference: str) -> tuple[str, str, str]:
    """Split a reference into API endpoint, repository, and tag or digest."""
    registry, remainder = reference.split("/", 1)
    repository, _, reference_name = remainder.rpartition(":")
    # docker.io is the registry's name; its API answers on registry-1.
    endpoint = DOCKER_HUB if registry == "docker.io" else registry
    return endpoint, repository, reference_name


def verify(entry: dict) -> list[str]:
    """Return the field-level differences between the entry and the registries."""
    if entry.get("source_type") == "local_docker_image":
        return verify_local(entry)

    source = registry_image(*split(entry["source"]))
    canonical = registry_image(*split(entry["canonical"]))

    observed = {
        "source_manifest_digest": source["manifest_digest"],
        "canonical_manifest_digest": canonical["manifest_digest"],
        "canonical_digest_reference":
            entry["canonical"].rsplit(":", 1)[0] + "@" + canonical["manifest_digest"],
        "config_digest": canonical["config_digest"],
        "source_config_digest": source["config_digest"],
        "config_digest_matches": canonical["config_digest"] == source["config_digest"],
        "compressed_layer_digests_match":
            canonical["layer_digests"] == source["layer_digests"],
        "rootfs_diff_id_count": canonical["rootfs_diff_id_count"],
        "platform": canonical["platform"],
    }
    differences = [f"{key}: recorded {entry.get(key)!r}, served {value!r}"
                   for key, value in observed.items() if entry.get(key) != value]
    entry["_observed"] = observed
    return differences


def verify_local(entry: dict) -> list[str]:
    """An archived local build has no registry source; only the GHCR copy is readable.

    Its contract is that the transferred config digest still equals the original image ID
    and that the declared rootfs layer identities survived the transfer.
    """
    canonical = registry_image(*split(entry["canonical"]))
    observed = {
        "canonical_manifest_digest": canonical["manifest_digest"],
        "canonical_digest_reference":
            entry["canonical"].rsplit(":", 1)[0] + "@" + canonical["manifest_digest"],
        "config_digest": canonical["config_digest"],
        "rootfs_diff_id_count": canonical["rootfs_diff_id_count"],
        "platform": canonical["platform"],
    }
    differences = [f"{key}: recorded {entry.get(key)!r}, served {value!r}"
                   for key, value in observed.items() if entry.get(key) != value]
    if canonical["config_digest"] != entry["source"]:
        differences.append(f"config digest no longer equals the source image id "
                           f"{entry['source']}")
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
