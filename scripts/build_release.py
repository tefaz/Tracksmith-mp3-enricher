"""Build portable Linux x86_64 installers without bundling host glibc or credentials."""

from __future__ import annotations

import hashlib
import io
import lzma
import os
import platform
import shutil
import subprocess
import tarfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "release"
DOWNLOADS = BUILD / "downloads"
DIST = ROOT / "dist"
PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261003/"
    "cpython-3.12.15%2B20261003-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
PYTHON_SHA256 = "731af898886c5f821890dc901eca3c651cca8e51fa7308c159d12a1194aeac91"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
APPIMAGETOOL_SHA256 = "95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6"
RUNTIME_URL = (
    "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
)
RUNTIME_SHA256 = "156f4bdbde9c52d01814600013e0a273f0118dc2de98975f3c8c63427ec79074"
NATIVE_PACKAGES = (
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-render-util0",
    "libxcb-util1",
    "libxkbcommon-x11-0",
    "libxcb-xkb1",
)
DEB_DEPENDS = (
    "libc6 (>= 2.34), libstdc++6 (>= 11), libgl1, libegl1, libopengl0, "
    "libx11-6, libx11-xcb1, libxrandr2, libxext6, libxcb1, libxcb-shm0, "
    "libxcb-sync1, libxcb-render0, libxcb-randr0, libxcb-shape0, libxcb-xfixes0, "
    "libxkbcommon0, libfontconfig1, libfreetype6, libdbus-1-3, libglib2.0-0 | libglib2.0-0t64, "
    "libnss3, libnspr4, libasound2 | libasound2t64, libpulse0, libgssapi-krb5-2, "
    "libdrm2, libzstd1, zlib1g, libbrotli1, ffmpeg"
)


def run(*command, **kwargs):
    subprocess.run([str(value) for value in command], check=True, **kwargs)


def download(url, name, expected=None):
    path = DOWNLOADS / name
    if not path.exists():
        print(f"Downloading {name}", flush=True)
        urllib.request.urlretrieve(url, path)
    if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Checksum mismatch for {name}; remove it and retry")
    return path


def native_libraries(payload):
    packages_path = download(
        "https://deb.debian.org/debian/dists/bookworm/main/binary-amd64/Packages.xz",
        "debian-bookworm-Packages.xz",
    )
    wanted = set(NATIVE_PACKAGES)
    records = {}
    for block in lzma.decompress(packages_path.read_bytes()).decode().split("\n\n"):
        record = dict(
            line.split(": ", 1)
            for line in block.splitlines()
            if ": " in line and not line.startswith(" ")
        )
        if record.get("Package") in wanted:
            records[record["Package"]] = record
    if set(records) != wanted:
        raise ValueError("Native dependency index is incomplete")
    destination = payload / "native-lib"
    destination.mkdir(exist_ok=True)
    for package, record in records.items():
        archive = download(
            "https://deb.debian.org/debian/" + record["Filename"],
            Path(record["Filename"]).name,
            record["SHA256"],
        )
        members = subprocess.check_output(["ar", "t", str(archive)], text=True).splitlines()
        data_member = next(name for name in members if name.startswith("data.tar."))
        data = subprocess.check_output(["ar", "p", str(archive), data_member])
        directory = BUILD / "native" / package
        directory.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(directory, filter="data")
        for library in directory.glob("usr/lib/x86_64-linux-gnu/*.so*"):
            target = destination / library.name
            if not target.exists() and not target.is_symlink():
                shutil.copy2(library, target, follow_symlinks=False)
        copyright_path = directory / "usr/share/doc" / package / "copyright"
        if copyright_path.exists():
            licenses = payload / "licenses" / package
            licenses.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copyright_path, licenses / "copyright")


def root_owned_tar(source, output):
    def owner(info):
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    with tarfile.open(output, "w:gz", compresslevel=6) as tar:
        tar.add(source, arcname=".", filter=owner)


def main():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This builder produces Linux x86_64 installers")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(exist_ok=True)
    payload = BUILD / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir()
    python_archive = download(PYTHON_URL, "python.tar.gz", PYTHON_SHA256)
    with tarfile.open(python_archive) as tar:
        tar.extractall(payload, filter="data")
    python = payload / "python/bin/python3"
    run(
        python,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "-r",
        ROOT / "packaging/requirements-base.txt",
    )
    shutil.copytree(
        ROOT / "src/tracksmith",
        payload / "app/tracksmith",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for source, target in (
        ("packaging/launch.sh", "tracksmith"),
        ("packaging/requirements-ai.txt", "requirements-ai.txt"),
        ("packaging/COPYRIGHT", "COPYRIGHT"),
        ("README.md", "README.md"),
    ):
        shutil.copy2(ROOT / source, payload / target)
    (payload / "tracksmith").chmod(0o755)
    native_libraries(payload)
    freeze = subprocess.check_output([str(python), "-m", "pip", "freeze"], text=True)
    (payload / "BUNDLED-PACKAGES.txt").write_text(freeze)
    for cache in payload.rglob("__pycache__"):
        shutil.rmtree(cache)
    run(payload / "tracksmith", "--version")
    icon = BUILD / "tracksmith.png"
    run(
        python,
        "-c",
        "from PIL import Image; import sys; "
        "Image.open(sys.argv[1]).resize((256, 256), Image.Resampling.LANCZOS).save(sys.argv[2])",
        ROOT / "src/tracksmith/assets/app-icon.png",
        icon,
    )

    appdir = BUILD / "Tracksmith.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr").mkdir(parents=True)
    shutil.copytree(payload, appdir / "usr/tracksmith", symlinks=True)
    shutil.copy2(ROOT / "packaging/tracksmith.desktop", appdir / "tracksmith.desktop")
    shutil.copy2(icon, appdir / "tracksmith.png")
    (appdir / ".DirIcon").symlink_to("tracksmith.png")
    (appdir / "AppRun").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'app_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'exec "$app_dir/usr/tracksmith/tracksmith" "$@"\n'
    )
    (appdir / "AppRun").chmod(0o755)
    tool = download(APPIMAGETOOL_URL, "appimagetool.AppImage", APPIMAGETOOL_SHA256)
    tool.chmod(0o755)
    runtime = download(RUNTIME_URL, "runtime-x86_64", RUNTIME_SHA256)
    appimage = DIST / f"Tracksmith-{version}-x86_64.AppImage"
    run(
        tool,
        "--appimage-extract-and-run",
        "--no-appstream",
        "--runtime-file",
        runtime,
        "--mksquashfs-opt",
        "-processors",
        "--mksquashfs-opt",
        "2",
        appdir,
        appimage,
        env={
            **os.environ,
            "ARCH": "x86_64",
            "VERSION": version,
            "APPIMAGELAUNCHER_DISABLE": "1",
        },
    )

    debroot = BUILD / "debroot"
    if debroot.exists():
        shutil.rmtree(debroot)
    (debroot / "opt").mkdir(parents=True)
    shutil.copytree(payload, debroot / "opt/tracksmith", symlinks=True)
    (debroot / "usr/bin").mkdir(parents=True)
    (debroot / "usr/bin/tracksmith").symlink_to("../../opt/tracksmith/tracksmith")
    for relative, source in (
        ("usr/share/applications/tracksmith.desktop", "packaging/tracksmith.desktop"),
        ("usr/share/doc/tracksmith/copyright", "packaging/COPYRIGHT"),
        (
            "usr/share/icons/hicolor/256x256/apps/tracksmith.png",
            "src/tracksmith/assets/app-icon.png",
        ),
    ):
        target = debroot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon if source.endswith("app-icon.png") else ROOT / source, target)
    control = BUILD / "control"
    control.mkdir(exist_ok=True)
    installed_size = (
        sum(path.stat().st_size for path in debroot.rglob("*") if path.is_file()) // 1024
    )
    (control / "control").write_text(
        f"Package: tracksmith\nVersion: {version}\nArchitecture: amd64\nSection: sound\nPriority: optional\n"
        f"Maintainer: Mads Nørgaard <madds.noergaard@gmail.com>\nInstalled-Size: {installed_size}\n"
        f"Depends: {DEB_DEPENDS}\nHomepage: https://github.com/tefaz/Tracksmith-mp3-enricher\n"
        "Description: Embed lyrics, line and word timings, and album covers in MP3s\n"
        " Includes batch lyric and cover lookup, manual timing, and optional local AI analysis.\n"
    )
    (BUILD / "debian-binary").write_text("2.0\n")
    root_owned_tar(control, BUILD / "control.tar.gz")
    root_owned_tar(debroot, BUILD / "data.tar.gz")
    deb = DIST / f"tracksmith_{version}_amd64.deb"
    if deb.exists():
        deb.unlink()
    run("ar", "rc", deb, BUILD / "debian-binary", BUILD / "control.tar.gz", BUILD / "data.tar.gz")
    checksums = DIST / "SHA256SUMS"
    hashes = []
    for path in (appimage, deb):
        with path.open("rb") as stream:
            hashes.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    checksums.write_text("".join(hashes))
    print(f"Release artifacts ready in {DIST}", flush=True)


if __name__ == "__main__":
    main()
