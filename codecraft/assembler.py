import os
import shutil
import re
import subprocess
import json
from pathlib import Path

# Default location of modules bundled with the package
MODULE_DIR = os.environ.get("CODECRAFT_MODULES", os.path.join(os.path.dirname(__file__), "modules"))

# Mapping from category to module file paths inside modules/
MODULE_MAP = {
    "backend": [
        "backend/flask/auth.py",
        "backend/flask/db.py",
        "backend/flask/routes.py",
    ],
    "frontend": [
        "frontend/react/index.js",
        "frontend/react/dashboard.js",
        "frontend/react/index.html",
    ],
    "mobile": [
        "mobile/flutter/main.dart",
    ],
    "cli": [
        "cli/python/commands.py",
    ],
}

# Minimal dependency templates for generated projects (expandable)
DEPENDENCY_MAP = {
    "backend/flask": {
        "requirements.txt": "flask>=2.0.0\npython-dotenv>=0.21.0\n",
    },
    "frontend/react": {
        "package.json": (
            '{\n'
            '  "name": "codecraft-app",\n'
            '  "version": "1.0.0",\n'
            '  "private": true,\n'
            '  "dependencies": {\n'
            '    "react": "^18.2.0",\n'
            '    "react-dom": "^18.2.0",\n'
            '    "axios": "^1.4.0"\n'
            '  },\n'
            '  "scripts": {\n'
            '    "start": "react-scripts start",\n'
            '    "build": "react-scripts build"\n'
            '  }\n'
            '}\n'
        ),
        "index.html": (
            "<!doctype html>\n"
            "<html>\n"
            "  <head>\n"
            "    <meta charset='utf-8'/>
    <meta name='viewport' content='width=device-width, initial-scale=1'/>
            "    <title>CodeCraft App</title>\n"
            "    <script src='https://cdn.tailwindcss.com'></script>\n"
            "  </head>\n"
            "  <body>\n"
            "    <div id='root'></div>\n"
            "    <script src='index.js'></script>\n"
            "  </body>\n"
            "</html>\n"
        ),
    },
    "mobile/flutter": {
        "pubspec.yaml": (
            "name: codecraft_app\n"
            "description: Auto-assembled Flutter app\n"
            "publish_to: none\n"
            "environment:\n"
            "  sdk: '>=2.17.0 <3.0.0'\n"
            "dependencies:\n"
            "  flutter:\n"
            "    sdk: flutter\n"
            "  http: ^0.13.0\n"
            "  shared_preferences: ^2.0.0\n"
        )
    },
    "cli/python": {
        "requirements.txt": "click>=8.0.0\nrequests>=2.28.0\n",
    },
}

def parse_description(desc: str):
    """
    Extract tags from string and return cleaned description and tag list.
    Tags look like: #mobile_friendly or #backend:fastapi
    """
    tags_matches = re.findall(r"#(\w+(:\w+)?)", desc)
    tags = [m[0] for m in tags_matches]
    clean_desc = re.sub(r"#\w+(:\w+)?", "", desc).strip()
    return clean_desc, tags

def select_modules(description: str, tags: list):
    """
    Basic keyword & tag based selection. Returns list of module relative paths.
    """
    selected = []
    desc_lower = (description or "").lower()
    if any(w in desc_lower for w in ["web", "dashboard", "ui", "frontend"]):
        selected.extend(MODULE_MAP.get("frontend", []))
        selected.extend(MODULE_MAP.get("backend", []))  # assume a web app needs a backend
    if any(w in desc_lower for w in ["api", "server", "backend"]):
        selected.extend(MODULE_MAP.get("backend", []))
    if any(w in desc_lower for w in ["mobile", "phone", "app"]):
        selected.extend(MODULE_MAP.get("mobile", []))
    if "cli" in desc_lower:
        selected.extend(MODULE_MAP.get("cli", []))
    for tag in tags:
        if tag == "mobile_friendly":
            selected.extend(MODULE_MAP.get("mobile", []))
        elif tag.startswith("backend:"):
            _, kind = tag.split(":", 1)
            if kind == "fastapi":
                # swap to fastapi modules if available
                selected = [m for m in selected if "flask" not in m]
                selected.extend([
                    "backend/fastapi/auth.py",
                    "backend/fastapi/routes.py",
                ])
    # deduplicate preserving order
    seen = set()
    result = []
    for m in selected:
        if m not in seen:
            result.append(m)
            seen.add(m)
    return result

def _copy_module(src_root: str, rel_path: str, dest_root: str):
    src_path = os.path.join(src_root, rel_path)
    if not os.path.exists(src_path):
        return False
    dest_path = os.path.join(dest_root, rel_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy(src_path, dest_path)
    return True

def assemble_project(project_name: str, modules: list, description: str, modules_selected: list):
    """
    Copy selected modules from MODULE_DIR into a new project folder, generate README,
    create dependency files and setup scripts (setup.sh, setup.bat), and create a package.json / requirements if needed.
    Returns the project path.
    """
    project_dir = os.path.abspath(project_name)
    os.makedirs(project_dir, exist_ok=True)

    # copy module files
    missing = []
    for m in modules:
        ok = _copy_module(MODULE_DIR, m, project_dir)
        if not ok:
            missing.append(m)

    # README
    readme_lines = []
    readme_lines.append(f"# {description or project_name}")
    readme_lines.append("")
    readme_lines.append("Assembled by CodeCraft CLI.")
    readme_lines.append("")
    readme_lines.append("Included modules:")
    for m in modules_selected:
        readme_lines.append(f"- {m}")
    readme_lines.append("")
    readme_lines.append("Setup instructions:")
    readme_lines.append("- Unix: ./setup.sh")
    readme_lines.append("- Windows: setup.bat")
    readme_content = "\n".join(readme_lines)
    with open(os.path.join(project_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme_content)

    # generate dependency files & setup scripts
    setup_sh = ["#!/usr/bin/env bash", "set -e", "echo \"Running setup...\""]
    setup_bat = ["@echo off", "echo Running setup..."]
    for m in modules:
        base = os.path.dirname(m)
        deps = DEPENDENCY_MAP.get(base, {})
        for filename, content in deps.items():
            with open(os.path.join(project_dir, filename), "w", encoding="utf-8") as f:
                f.write(content)
            if filename.endswith("requirements.txt"):
                setup_sh.append("python -m pip install -r requirements.txt")
                setup_bat.append("python -m pip install -r requirements.txt")
            elif filename == "package.json":
                setup_sh.append("npm install")
                setup_bat.append("npm install")
            elif filename == "pubspec.yaml":
                setup_sh.append("flutter pub get")
                setup_bat.append("flutter pub get")

    # write setup scripts
    with open(os.path.join(project_dir, "setup.sh"), "w", encoding="utf-8") as f:
        f.write("\n".join(setup_sh) + "\n")
    with open(os.path.join(project_dir, "setup.bat"), "w", encoding="utf-8") as f:
        f.write("\r\n".join(setup_bat) + "\r\n")

    # list missing modules
    if missing:
        print("Warning: some requested modules were not found in the bundled modules directory:")
        for mm in missing:
            print(" -", mm)

    # create a small metadata file to help automation
    meta = {
        "project_name": project_name,
        "modules": modules_selected,
        "description": description,
    }
    with open(os.path.join(project_dir, ".codecraft_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return project_dir

def prepare_winget_manifest(project_dir: str, identifier: str, version: str = "0.1.0"):
    """
    Create a draft winget manifest YAML in project_dir/winget-manifest.yaml.
    You will still need to package a real EXE and compute SHA256.
    """
    manifest = {
        "PackageIdentifier": identifier,
        "PackageVersion": version,
        "PackageName": "CodeCraft CLI",
        "Publisher": "Your Name",
        "License": "MIT",
        "ShortDescription": "CodeCraft CLI - assemble multi-language apps from snippets",
        "Installers": [
            {
                "Architecture": "x64",
                "InstallerType": "exe",
                "InstallerUrl": "https://github.com/YOUR_USER/YOUR_REPO/releases/download/v{}/codecraft-cli.exe".format(version),
                "InstallerSha256": "<TO_COMPUTE_SHA256>"
            }
        ],
        "ManifestType": "singleton",
        "ManifestVersion": "1.0.0"
    }
    import yaml  # pyyaml is optional for manifest creation; if not present, write plain JSON-like YAML
    try:
        with open(os.path.join(project_dir, "winget-manifest.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f, sort_keys=False)
    except Exception:
        # fallback to simple text output
        with open(os.path.join(project_dir, "winget-manifest.yaml"), "w", encoding="utf-8") as f:
            f.write(str(manifest))
    return os.path.join(project_dir, "winget-manifest.yaml")

def prepare_chocolatey_nuspec(project_dir: str, package_id: str = "codecraft-cli", version: str = "0.1.0"):
    """
    Create a basic Chocolatey package skeleton in project_dir/choco/ to help manual packaging/submission.
    """
    tools_dir = os.path.join(project_dir, "choco")
    os.makedirs(tools_dir, exist_ok=True)
    nuspec = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd">
  <metadata>
    <id>{package_id}</id>
    <version>{version}</version>
    <authors>Your Name</authors>
    <description>CodeCraft CLI - assemble multi-language apps from snippets</description>
    <projectUrl>https://github.com/YOUR_USER/YOUR_REPO</projectUrl>
    <licenseUrl>https://github.com/YOUR_USER/YOUR_REPO/blob/main/LICENSE</licenseUrl>
  </metadata>
</package>"""
    with open(os.path.join(tools_dir, f"{package_id}.nuspec"), "w", encoding="utf-8") as f:
        f.write(nuspec)
    install_ps1 = """$ErrorActionPreference = 'Stop'
Write-Output 'Installing CodeCraft CLI via pip...'
python -m pip install codecraft-cli
"""
    with open(os.path.join(tools_dir, "chocolateyInstall.ps1"), "w", encoding="utf-8") as f:
        f.write(install_ps1)
    return tools_dir
