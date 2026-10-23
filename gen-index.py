#!/usr/bin/env python3
"""
gen-index.py — Generate index.toml dari kumpulan file .tetopkg di suatu folder.
Format mengikuti struct RepoIndex/RepoMeta/RepoPackage di crates/teto-repo-common/src/index.rs
"""
import sys
import os
import glob
import hashlib
import subprocess
import time
import re
import argparse
import tomllib  # python 3.11+
import tomli_w  # pip install tomli_w

try:
    from packaging.version import parse as parse_version
except ImportError:
    def parse_version(ver_str: str):
        """Fallback version parser extracting numeric tuple components."""
        nums = [int(n) for n in re.findall(r'\d+', str(ver_str))]
        if nums:
            return tuple(nums)
        return (str(ver_str),)

def get_pkg_version_key(pkg_entry: dict):
    """
    Computes a comparable tuple (version_key, pkgrel) for sorting and deduplicating packages.
    """
    pkg = pkg_entry["package"]
    ver = pkg.get("version", "0.0.0")
    pkgrel = pkg.get("pkgrel", 1)
    try:
        ver_key = parse_version(ver)
    except Exception as e:
        print(f"[WARN] Failed to parse semver for version '{ver}' of package '{pkg.get('name')}': {e}. Falling back to string comparison.")
        ver_key = (ver,)
    return (ver_key, pkgrel)

def read_pkginfo(tetopkg_path: str) -> dict:
    """Extract .PKGINFO.toml dari dalam arsip .tetopkg (tar + zstd)."""
    result = subprocess.run(
        f"zstd -dc '{tetopkg_path}' | tar -xO --wildcards '.PKGINFO.toml'",
        shell=True, capture_output=True, check=True
    )
    return tomllib.loads(result.stdout.decode("utf-8"))

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(
        description="Generate index.toml from .tetopkg files in a repository directory."
    )
    parser.add_argument("repo_dir", help="Directory containing .tetopkg files")
    parser.add_argument("output_path", help="Output path for index.toml")
    parser.add_argument("repo_name", help="Repository name")
    parser.add_argument("repo_desc", help="Repository description")
    parser.add_argument(
        "--srcpkgs",
        default="srcpkgs",
        help="Path to srcpkgs directory to filter valid packages",
    )
    parser.add_argument(
        "--prune-files",
        action="store_true",
        default=False,
        help="Delete .tetopkg files from disk if their package is not in srcpkgs",
    )
    parser.add_argument(
        "--keep-all-versions",
        action="store_true",
        default=False,
        help="Disable deduplication and keep all package versions in index.toml",
    )
    parser.add_argument(
        "--repo-url",
        default="https://petani.repository.eu.org/x86_64/",
        help="Repository base URL",
    )

    args = parser.parse_args()

    valid_pkgs = set()
    if os.path.isdir(args.srcpkgs):
        valid_pkgs = {
            d for d in os.listdir(args.srcpkgs)
            if os.path.isfile(os.path.join(args.srcpkgs, d, "template"))
        }
        print(f":: Loaded {len(valid_pkgs)} valid package templates from {args.srcpkgs}")

    raw_packages = []
    for pkg_path in sorted(glob.glob(os.path.join(args.repo_dir, "*.tetopkg"))):
        try:
            info = read_pkginfo(pkg_path)
        except Exception as e:
            print(f":: [ERROR] Failed to read {pkg_path}: {e}")
            continue

        pkg = info["package"]
        deps = info.get("deps", {})
        pkg_name = pkg["name"]

        # If srcpkgs is present, filter out packages that were deleted/renamed
        if valid_pkgs and pkg_name not in valid_pkgs:
            print(f":: [ORPHAN] Skipping '{pkg_name}' (not in {args.srcpkgs})")
            if args.prune_files:
                print(f":: [PRUNE] Deleting orphaned file {pkg_path}")
                try:
                    os.remove(pkg_path)
                except Exception as e:
                    print(f":: [WARN] Failed to delete {pkg_path}: {e}")
            continue

        raw_packages.append({
            "package": {
                "name": pkg["name"],
                "version": pkg["version"],
                "pkgrel": pkg.get("pkgrel", 1),
                "arch": pkg["arch"],
                "desc": pkg["desc"],
                "url": pkg["url"],
                "license": pkg["license"],
                "size": pkg.get("size", 0),
                "builddate": pkg.get("builddate", int(time.time())),
                "packager": pkg.get("packager", "gen-index"),
            },
            "deps": {
                "requires": deps.get("requires", []),
                "optional": deps.get("optional", []),
                "conflicts": deps.get("conflicts", []),
                "provides": deps.get("provides", []),
            },
        })

    # Deduplikasi dilakukan per package name agar index.toml hanya berisi
    # versi tertinggi per package secara default. Jika folder repo menyimpan multiple build lama,
    # tanpa deduplikasi semua versi akan masuk index.toml dan dapat menyebabkan client resolver
    # salah memilih versi lama. Gunakan --keep-all-versions jika ingin mengekspos semua versi.
    if not args.keep_all_versions:
        grouped = {}
        for pkg_entry in raw_packages:
            name = pkg_entry["package"]["name"]
            if name not in grouped:
                grouped[name] = pkg_entry
            else:
                existing_key = get_pkg_version_key(grouped[name])
                new_key = get_pkg_version_key(pkg_entry)
                if new_key > existing_key:
                    grouped[name] = pkg_entry
        packages = list(grouped.values())
    else:
        packages = raw_packages

    index = {
        "meta": {
            "name": args.repo_name,
            "description": args.repo_desc,
            "url": args.repo_url,
            "generated": int(time.time()),
        },
        "packages": packages,
    }

    with open(args.output_path, "wb") as f:
        tomli_w.dump(index, f)

    print(f":: Wrote {args.output_path} ({len(packages)} packages)")

if __name__ == "__main__":
    main()
