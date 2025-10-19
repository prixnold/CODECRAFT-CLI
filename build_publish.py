#!/usr/bin/env python3
"""
build_publish.py

Local automation to:
 - build the Python wheel (using build)
 - create a standalone EXE (using PyInstaller)
 - prepare Winget and Chocolatey draft artifacts
 - optionally upload to PyPI (requires token)

Usage:
  python build_publish.py --build
  python build_publish.py --build --make-exe
  python build_publish.py --publish-pypi --pypi-token <token>
"""
import argparse
import os
import subprocess
import sys
import shutil
import getpass
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

def run(cmd, cwd=None, check=True):
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd or ROOT, check=check)

def build_wheel():
    print("Building wheel...")
    run([sys.executable, "-m", "build", "--wheel", "--no-isolation"], cwd=ROOT)
    dist = ROOT / "dist"
    if dist.exists():
        wheels = list(dist.glob("*.whl"))
        if wheels:
            print("Built:", wheels[0])
            return wheels[0]
    return None

def make_exe():
    print("Creating EXE with PyInstaller (this may take a while)...")
    run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    spec_name = "codecraft_cli"
    run([sys.executable, "-m", "PyInstaller", "--onefile", "--name", spec_name, "-c", "python -m codecraft.cli"], cwd=ROOT)
    print("EXE artifacts should be in dist/")
    return ROOT / "dist"

def publish_pypi(wheel_path, token):
    print("Uploading to PyPI (twine).")
    run([sys.executable, "-m", "pip", "install", "twine"])
    run([sys.executable, "-m", "twine", "upload", str(wheel_path), "-u", "__token__", "-p", token])

def create_release_zip():
    import zipfile
    zip_path = ROOT / "release_artifacts.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        # include wheel(s)
        for w in (ROOT / "dist").glob("*.whl"):
            z.write(w, arcname=w.name)
        # include exe(s)
        for e in (ROOT / "dist").glob("*"):
            if e.is_file():
                z.write(e, arcname=e.name)
    print("Created ZIP:", zip_path)
    return zip_path

def prepare_manifests():
    # produce winget manifest and chocolatey skeleton
    from codecraft.assembler import prepare_winget_manifest, prepare_chocolatey_nuspec
    project_dir = ROOT
    wm = prepare_winget_manifest(str(project_dir), identifier="YourName.CodeCraftCLI", version="0.1.0")
    ch = prepare_chocolatey_nuspec(str(project_dir), package_id="codecraft-cli", version="0.1.0")
    print("Winget manifest:", wm)
    print("Chocolatey choco folder:", ch)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--make-exe", action="store_true")
    parser.add_argument("--publish_pypi", action="store_true")
    parser.add_argument("--pypi-token", help="PyPI token for upload (or set PYPI_TOKEN env var)")
    args = parser.parse_args()

    wheel = None
    if args.build:
        wheel = build_wheel()

    if args.make_exe:
        make_exe()

    if args.publish_pypi:
        token = args.pypi_token or os.environ.get("PYPI_TOKEN")
        if not token:
            token = getpass.getpass("Enter PyPI token: ")
        if not wheel:
            wheels = list((ROOT / "dist").glob("*.whl"))
            if not wheels:
                print("No wheel found to upload. Build first with --build.")
                return
            wheel = wheels[0]
        publish_pypi(wheel, token)

    print("Done. Review dist/ and follow README for next steps.")

if __name__ == "__main__":
    main()