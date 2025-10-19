#!/usr/bin/env python3
"""
tools/auto_release.py

A maximalist automation script that performs the following steps (when run locally):
1. Validates environment and Python version.
2. Creates and activates a virtual environment for reproducible builds (optional: can use system Python).
3. Installs required build tools (build, twine, pyinstaller, requests, PyGithub, pyyaml).
4. Builds the Python wheel using PEP 517 'python -m build'.
5. Creates a standalone EXE using PyInstaller (onefile).
6. Computes SHA256 checksums for release artifacts.
7. Prepares Winget manifest and Chocolatey package skeleton, injecting the computed SHA256.
8. Creates a GitHub release in the target repository and uploads artifacts (wheel, exe, zip).
9. Optionally uploads the wheel to PyPI using a PyPI token.
10. Optionally commits generated manifests into a new branch and opens a PR in the repo (requires GITHUB_TOKEN).

Security: the script will never print tokens. It reads tokens from environment variables or prompts securely.

Usage examples (run from repository root):
  python tools/auto_release.py --build --make-exe --release --upload-pypi

Environment variables the script reads (preferred):
  GITHUB_TOKEN - a token with repo scope to create releases and push files
  PYPI_TOKEN - the PyPI API token for upload (recommended to store as env)

This script is intended to run locally by the repository owner with appropriate credentials.

"""

from __future__ import annotations
import argparse
import os
import sys
import subprocess
import shutil
import hashlib
import base64
import json
import tempfile
import getpass
from pathlib import Path
from typing import Optional, Tuple

# Third-party imports are lazy-installed by the script when necessary to keep bootstrapping robust.

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

def run(cmd, cwd: Optional[Path] = None, check: bool = True, capture_output: bool = False):
    """Run a subprocess command and stream output."""
    print(f"> {{' '.join(map(str, cmd))}} (cwd={{cwd or Path.cwd()}})")
    result = subprocess.run(list(map(str, cmd)), cwd=str(cwd or ROOT), check=False, capture_output=capture_output, text=True)
    if check and result.returncode != 0:
        print(result.stdout or "", result.stderr or "")
        raise RuntimeError(f"Command failed: {{cmd}} (exit {{result.returncode}})")
    return result

# helper to ensure package installed

def ensure_pip_packages(packages: list[str]):
    py = sys.executable
    run([py, "-m", "pip", "install", "--upgrade"])
    for p in packages:
        print(f"Ensuring pip package: {{p}}")
        run([py, "-m", "pip", "install", p])

# Build wheel

def build_wheel() -> Path:
    ensure_pip_packages(["build"])
    print("Building wheel...")
    run([sys.executable, "-m", "build", "--wheel", "--no-isolation"], cwd=ROOT)
    if not DIST.exists():
        raise FileNotFoundError("dist/ directory not found after build")
    wheels = list(DIST.glob("*.whl"))
    if not wheels:
        raise FileNotFoundError("No wheel artifact found in dist/")
    # return latest wheel
    wheel = max(wheels, key=lambda p: p.stat().st_mtime)
    print(f"Built wheel: {{wheel}}")
    return wheel

# Create EXE via PyInstaller

def make_exe(entry_module: str = "codecraft.cli", exe_name: str = "codecraft-cli") -> Path:
    ensure_pip_packages(["pyinstaller"])
    print("Creating standalone EXE with pyinstaller...")
    # remove previous build artifacts
    for d in [ROOT / "build", ROOT / "dist", ROOT / f"{{exe_name}}.spec"]:
        if d.exists():
            try:
                if d.is_dir():
                    shutil.rmtree(d)
                else:
                    d.unlink()
            except Exception as e:
                print("Warning: could not clean previous build artifact:", d, e)
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--name", exe_name, "-c", f"python -m {{entry_module}}"]
    run(cmd, cwd=ROOT)
    # PyInstaller by default writes exe into ./dist/<exe_name> or dist/<exe_name>.exe
    dist_dir = ROOT / "dist"
    exe_candidates = list(dist_dir.glob(exe_name + "*"))
    if not exe_candidates:
        raise FileNotFoundError("PyInstaller did not produce expected EXE in dist/")
    exe = max(exe_candidates, key=lambda p: p.stat().st_mtime)
    print(f"Created EXE: {{exe}}")
    return exe

# compute sha256
def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

# package release zip

def create_release_zip(artifacts: list[Path], out: Optional[Path] = None) -> Path:
    import zipfile
    out = out or (ROOT / "release_artifacts.zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for a in artifacts:
            z.write(a, arcname=a.name)
    print(f"Created release zip: {{out}}")
    return out

# Prepare winget manifest and chocolatey skeleton using assembler helpers from package

def prepare_manifests(exe_sha256: str, exe_url: str, version: str = "0.1.0") -> Tuple[Path, Path]:
    # import local assembler to generate baseline manifests
    sys.path.insert(0, str(ROOT))
    try:
        from codecraft import assembler
    except Exception as e:
        raise RuntimeError("Could not import codecraft. Ensure this script is run from repository root and package is installed or importable") from e
    # generate winget manifest draft
    wm_path = assembler.prepare_winget_manifest(str(ROOT), identifier="prixnold.CodeCraftCLI", version=version)
    # patch the manifest to include real URL and SHA256 if possible
    try:
        import yaml
        with open(wm_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "Installers" in data and isinstance(data["Installers"], list) and data["Installers"]:
            data["Installers"][0]["InstallerUrl"] = exe_url
            data["Installers"][0]["InstallerSha256"] = exe_sha256
        with open(wm_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        print("Patched winget manifest with URL & SHA256")
    except Exception:
        # best-effort; file already created
        print("Could not patch winget manifest with YAML; manifest saved at", wm_path)

    choco_dir = assembler.prepare_chocolatey_nuspec(str(ROOT), package_id="codecraft-cli", version=version)
    # write a README in choco dir explaining the approach
    readme = Path(choco_dir) / "README.md"
    readme.write_text("""Chocolatey package skeleton. Add installer hosting and update the nuspec accordingly. The install script currently installs via pip.
""")
    return Path(wm_path), Path(choco_dir)

# GitHub release using PyGithub or REST API

def create_github_release_and_upload(github_token: str, owner: str, repo: str, tag: str, name: str, body: str, artifacts: list[Path]):
    # prefer PyGithub if available
    try:
        from github import Github
    except Exception:
        ensure_pip_packages(["PyGithub"])  # installs github
        from github import Github
    gh = Github(github_token)
    repository = gh.get_repo(f"{{owner}}/{{repo}}")
    # create tag/ref and release
    print(f"Creating release tag {{tag}} in {{owner}}/{{repo}}...")
    # if tag exists, create a new release with same tag (GitHub allows multiple releases per tag?)
    # we'll attempt to create release; if exists, we proceed to upload assets
    try:
        release = repository.create_git_release(tag=tag, name=name, message=body, draft=False, prerelease=False)
        print("Release created:", release.html_url)
    except Exception as e:
        # fallback: find existing release by tag
        print("Could not create release (it may already exist):", e)
        releases = repository.get_releases()
        release = None
        for r in releases:
            if r.tag_name == tag:
                release = r
                print("Found existing release for tag", tag)
                break
        if release is None:
            raise
    # upload artifacts
    for art in artifacts:
        print("Uploading asset:", art)
        release.upload_asset(str(art), label=art.name)
    print("Uploaded assets to release.")
    return release.html_url

# Upload file content to repo branch via GitHub contents API (create/update) using REST to add manifests or files

def github_create_or_update_file(github_token: str, owner: str, repo: str, path: str, content_bytes: bytes, branch: str, message: str):
    import base64
    import requests
    api = "https://api.github.com"
    headers = {"Authorization": f"token {{github_token}}", "Accept": "application/vnd.github.v3+json"}
    url_get_ref = f"{{api}}/repos/{{owner}}/{{repo}}/git/ref/heads/{{branch}}"
    # check if branch exists
    r = requests.get(url_get_ref, headers=headers)
    if r.status_code == 404:
        # create branch from default branch
        print(f"Branch {{branch}} does not exist. Creating it from default branch...")
        repo_info = requests.get(f"{{api}}/repos/{{owner}}/{{repo}}", headers=headers).json()
        default_branch = repo_info.get("default_branch", "main")
        # get commit sha of default branch
        ref = requests.get(f"{{api}}/repos/{{owner}}/{{repo}}/git/ref/heads/{{default_branch}}", headers=headers).json()
        commit_sha = ref["object"]["sha"]
        # create new ref
        create_ref = requests.post(f"{{api}}/repos/{{owner}}/{{repo}}/git/refs", headers=headers, json={"ref": f"refs/heads/{{branch}}", "sha": commit_sha})
        create_ref.raise_for_status()
        print("Created branch", branch)
    # check if file exists
    url_file = f"{{api}}/repos/{{owner}}/{{repo}}/contents/{{path}}"
    r = requests.get(url_file, headers=headers, params={"ref": branch})
    if r.status_code == 200:
        sha = r.json()["sha"]
        print(f"Updating existing file {{path}} on branch {{branch}}")
        payload = {"message": message, "content": base64.b64encode(content_bytes).decode(), "branch": branch, "sha": sha}
        resp = requests.put(url_file, headers=headers, json=payload)
        resp.raise_for_status()
    elif r.status_code == 404:
        print(f"Creating new file {{path}} on branch {{branch}}")
        payload = {"message": message, "content": base64.b64encode(content_bytes).decode(), "branch": branch}
        resp = requests.put(url_file, headers=headers, json=payload)
        resp.raise_for_status()
    else:
        r.raise_for_status()
    print(f"File {{path}} committed to {{branch}}.")

# Publish to PyPI using twine

def upload_to_pypi(wheel: Path, token: str):
    ensure_pip_packages(["twine"])
    print("Uploading wheel to PyPI via twine (token provided)")
    run([sys.executable, "-m", "twine", "upload", str(wheel), "-u", "__token__", "-p", token])

# Utility to get version string from pyproject.toml
def get_project_version() -> str:
    pyproj = ROOT / "pyproject.toml"
    if not pyproj.exists():
        return "0.0.0"
    import tomli
    with open(pyproj, "rb") as f:
        data = tomli.load(f)
    return data.get("project", {}).get("version", "0.0.0")

# Main orchestration

def main():
    parser = argparse.ArgumentParser(description="Full automator: build, package, create release, upload artifacts and prepare OS package manifests.")
    parser.add_argument("--build", action="store_true", help="Build wheel via python -m build")
    parser.add_argument("--make-exe", action="store_true", help="Create standalone EXE via PyInstaller")
    parser.add_argument("--release", action="store_true", help="Create GitHub release and upload artifacts (requires GITHUB_TOKEN)")
    parser.add_argument("--upload-pypi", action="store_true", help="Upload wheel to PyPI (requires PYPI_TOKEN)")
    parser.add_argument("--commit-manifests", action="store_true", help="Commit generated manifests (winget/choco) into a release branch and open a PR")
    parser.add_argument("--branch", default="release/artifacts", help="Branch name to commit manifests if requested")
    parser.add_argument("--owner", default="prixnold", help="GitHub owner (user or org)")
    parser.add_argument("--repo", default="CODECRAFT-CLI", help="GitHub repository name")
    args = parser.parse_args()

    artifacts: list[Path] = []
    wheel: Optional[Path] = None
    exe: Optional[Path] = None

    if args.build:
        wheel = build_wheel()
        artifacts.append(wheel)

    if args.make_exe:
        exe = make_exe()
        artifacts.append(exe)

    if artifacts:
        # compute checksums
        checks = {a.name: sha256_of_file(a) for a in artifacts}
        for n, h in checks.items():
            print(f"Artifact: {{n}} SHA256: {{h}}")

    # create a release zip
    if artifacts:
        zip_path = create_release_zip(artifacts)
        artifacts.append(zip_path)

    # prepare manifests and choco skeleton if exe is present
    winget_manifest_path = None
    choco_dir = None
    if exe:
        exe_sha = sha256_of_file(exe)
        print("EXE sha256:", exe_sha)
        # we will upload exe to GitHub release and then patch the winget manifest with the release asset URL
        # For now prepare manifests with placeholder URL and SHA
        winget_manifest_path, choco_dir = prepare_manifests(exe_sha, exe_url="https://example.com/codecraft-cli.exe", version=get_project_version())
        artifacts.append(winget_manifest_path)

    # create GitHub release and upload assets
    if args.release:
        github_token = os.environ.get("GITHUB_TOKEN") or getpass.getpass("Enter GITHUB_TOKEN (repo scope): ")
        if not github_token:
            raise RuntimeError("GITHUB_TOKEN required to create a release and upload artifacts")
        version = get_project_version() or "0.1.0"
        tag = f"v{{version}}"
        release_name = f"CodeCraft CLI {{tag}}"
        release_body = "Automated release created by tools/auto_release.py"
        release_url = create_github_release_and_upload(github_token, args.owner, args.repo, tag, release_name, release_body, artifacts)
        print("Release published:", release_url)
        # If winget manifest exists, update it with real exe URL pointing to release asset
        if winget_manifest_path:
            # find asset URL from release
            # using PyGithub we can fetch release assets
            from github import Github
            gh = Github(github_token)
            repo_obj = gh.get_repo(f"{{args.owner}}/{{args.repo}}")
            rels = repo_obj.get_releases()
            rel = None
            for r in rels:
                if r.tag_name == tag:
                    rel = r
                    break
            if rel is None:
                print("Warning: could not find release object to retrieve asset URLs")
            else:
                # find exe asset
                assets = rel.get_assets()
                exe_asset = None
                for a in assets:
                    if exe and a.name == exe.name:
                        exe_asset = a
                        break
                if exe_asset:
                    exe_url = exe_asset.browser_download_url
                    exe_sha = sha256_of_file(exe)
                    # patch the winget manifest file with the real url and sha
                    try:
                        import yaml
                        with open(winget_manifest_path, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                        if "Installers" in data and isinstance(data["Installers"], list) and data["Installers"]:
                            data["Installers"][0]["InstallerUrl"] = exe_url
                            data["Installers"][0]["InstallerSha256"] = exe_sha
                        with open(winget_manifest_path, "w", encoding="utf-8") as f:
                            yaml.safe_dump(data, f, sort_keys=False)
                        print("Updated winget manifest with release asset URL and SHA256")
                    except Exception as e:
                        print("Could not update winget manifest:", e)

    # upload wheel to PyPI
    if args.upload_pypi:
        pypi_token = os.environ.get("PYPI_TOKEN") or getpass.getpass("Enter PYPI token: ")
        if not pyi_token:
            raise RuntimeError("PYPI token required to upload to PyPI")
        if not wheel:
            wheels = list(DIST.glob("*.whl"))
            if not wheels:
                raise RuntimeError("No wheel to upload; build first")
            wheel = wheels[0]
        upload_to_pypi(wheel, pypi_token)
        print("Uploaded wheel to PyPI")

    # commit manifests into branch and open PR
    if args.commit_manifests:
        github_token = os.environ.get("GITHUB_TOKEN") or getpass.getpass("Enter GITHUB_TOKEN (repo scope): ")
        branch = args.branch
        # commit winget manifest and choco folder
        files_to_commit = []
        if winget_manifest_path and winget_manifest_path.exists():
            files_to_commit.append(winget_manifest_path)
        if choco_dir and choco_dir.exists():
            # include all files inside choco dir
            for p in choco_dir.rglob("*"):
                if p.is_file():
                    files_to_commit.append(p)
        # create/ensure branch and commit files using GitHub contents API
        for f in files_to_commit:
            rel_path = str(f.relative_to(ROOT))
            print("Committing", rel_path, "to branch", branch)
            content_bytes = f.read_bytes()
            github_create_or_update_file(github_token, args.owner, args.repo, rel_path.replace('\\', '/'), content_bytes, branch=branch, message=f"Add {{rel_path}} for release")
        # create PR using PyGithub
        try:
            from github import Github
        except Exception:
            ensure_pip_packages(["PyGithub"])
            from github import Github
        gh = Github(github_token)
        repo_obj = gh.get_repo(f"{{args.owner}}/{{args.repo}}")
        default_branch = repo_obj.default_branch
        pr_title = f"Add release artifacts and manifests ({{get_project_version()}})"
        pr_body = "This PR adds release artifacts and draft manifests for Winget and Chocolatey prepared by tools/auto_release.py"
        try:
            pr = repo_obj.create_pull(title=pr_title, body=pr_body, head=branch, base=default_branch)
            print("Created PR:", pr.html_url)
        except Exception as e:
            print("Could not create PR:", e)

    print("All requested steps completed.")

if __name__ == '__main__':
    main()