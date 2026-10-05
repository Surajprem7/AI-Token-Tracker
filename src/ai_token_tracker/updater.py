"""Update check and one-click / automatic update.

The app asks GitHub for the latest release of this project (a plain GET with no
usage data; it can be switched off) and, when a newer version exists, can
install it:

* Windows, installed with AITokenTracker-Setup.exe: downloads the new installer
  and runs it silently; the installer closes and restarts the app.
* macOS app: downloads the new .app, swaps it in after the app quits, reopens it.
* pip install: upgrades from the release's wheel with pip, then restarts.
* Portable .exe and the Linux single-file app: shows where to download it.

It checks every time the app opens (and every 6 hours while it stays open), and
always installs updates automatically, so everyone gets fixes without having to
look for them.

Downloads are checked against the SHA-256 digest GitHub publishes for each file.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import __version__

REPO = "Surajprem7/AI-Token-Tracker"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY_SECONDS = 6 * 3600
WINDOWS_ASSET = "AITokenTracker-Setup.exe"
MAC_ASSET = "AITokenTracker-macOS-AppleSilicon.zip"


# --------------------------------------------------------------------------- #
# Last answer from GitHub (~/.ai-token-tracker/update-cache.json)
# --------------------------------------------------------------------------- #

_checked_since_launch = False  # every launch asks GitHub once, whatever the cache says


def _home() -> Path:
    return Path(os.environ.get("AI_TOKEN_TRACKER_DIR") or Path.home() / ".ai-token-tracker").expanduser()


def cache_file() -> Path:
    return _home() / "update-cache.json"


def _load_cache() -> dict:
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    path = cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Versions and the GitHub release
# --------------------------------------------------------------------------- #


def parse_version(text: str) -> tuple:
    """'v2.10.1' -> (2, 10, 1). Anything unparsable sorts lowest."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(text or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _ssl_context():
    from .pricing import _ssl_context as ctx

    return ctx()


def _fetch_json(url: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"ai-token-tracker/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:  # noqa: S310 - fixed https URL
        return json.loads(resp.read().decode("utf-8"))


def _release_info(release: dict) -> dict:
    assets = {}
    for a in release.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            assets[a["name"]] = {"url": a["browser_download_url"], "size": a.get("size") or 0,
                                 "digest": a.get("digest") or ""}
    return {"version": str(release.get("tag_name") or "").lstrip("v"), "notes": str(release.get("body") or "")[:4000],
            "page": release.get("html_url") or RELEASES_PAGE, "assets": assets}


def install_method() -> str:
    """How this copy can update itself: 'windows-installer', 'mac-app', 'pip' or 'manual'."""
    if not getattr(sys, "frozen", False):
        # Installed with pip (lives in site-packages) vs. run from a source checkout.
        here = Path(__file__).resolve()
        return "pip" if any(p.name in ("site-packages", "dist-packages") for p in here.parents) else "manual"
    exe = Path(sys.executable).resolve()
    if sys.platform == "win32":
        programs = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs"
        installed = programs.resolve() in exe.parents and (exe.parent / "unins000.exe").exists()
        return "windows-installer" if installed else "manual"
    if sys.platform == "darwin" and _mac_bundle(exe) is not None:
        return "mac-app"
    return "manual"


def _mac_bundle(exe: Path) -> Path | None:
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def manual_instructions() -> str:
    return f"Download the new version from {RELEASES_PAGE}"


def check(force: bool = False) -> dict:
    """Latest-version status for the dashboard.

    Asks GitHub on the first call after each launch, then at most every 6 hours
    while the app stays open (or whenever forced).
    """
    global _checked_since_launch
    status = {"current": __version__, "method": install_method(), "manual": manual_instructions(),
              "available": False, "latest": None, "error": None, "can_install": False}
    cache = _load_cache()
    stale = time.time() - float(cache.get("checked_at") or 0) >= CHECK_EVERY_SECONDS
    if force or not _checked_since_launch or stale or not cache.get("release"):
        _checked_since_launch = True
        try:
            release = _release_info(_fetch_json(LATEST_API))
            _save_cache({**cache, "checked_at": time.time(), "release": release})
        except Exception as exc:  # offline, rate-limited, no releases yet...
            status["error"] = f"Couldn't check for updates: {exc}"
            release = cache.get("release")
    else:
        release = cache["release"]
    if release and release.get("version"):
        status["latest"] = {k: release[k] for k in ("version", "notes", "page")}
        status["available"] = is_newer(release["version"])
        status["can_install"] = status["available"] and _asset_for(status["method"], release) is not None
        # Install by itself, unless this same update already failed twice today (then wait for a click),
        # so a failing installer can never close the app on every start.
        tries = cache.get("attempts") if isinstance(cache.get("attempts"), dict) else {}
        recent = tries.get(release["version"]) if isinstance(tries.get(release["version"]), dict) else {}
        failed_today = recent.get("count", 0) >= 2 and time.time() - recent.get("at", 0) < 86400
        status["auto"] = status["can_install"] and not failed_today
    return status


def _asset_for(method: str, release: dict) -> dict | None:
    assets = release.get("assets") or {}
    if method == "pip":
        return next((a for n, a in assets.items() if n.startswith("ai_token_tracker-") and n.endswith(".whl")), None)
    name = {"windows-installer": WINDOWS_ASSET, "mac-app": MAC_ASSET}.get(method)
    return assets.get(name) if name else None


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #


def _download(asset: dict, dest: Path) -> None:
    req = urllib.request.Request(asset["url"], headers={"User-Agent": f"ai-token-tracker/{__version__}"})
    sha = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp, dest.open("wb") as fh:  # noqa: S310
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            sha.update(chunk)
            fh.write(chunk)
    expected = asset.get("digest") or ""
    if expected.startswith("sha256:") and sha.hexdigest() != expected.split(":", 1)[1].lower():
        dest.unlink(missing_ok=True)
        raise ValueError("the downloaded file doesn't match GitHub's checksum; not installing it")
    if asset.get("size") and dest.stat().st_size != asset["size"]:
        dest.unlink(missing_ok=True)
        raise ValueError("the download is incomplete; not installing it")


def _note_attempt(version: str) -> None:
    """Count install attempts per version (if we're still on the old one later, it failed)."""
    cache = _load_cache()
    tries = cache.get("attempts") if isinstance(cache.get("attempts"), dict) else {}
    prev = tries.get(version) if isinstance(tries.get(version), dict) else {}
    fresh = time.time() - prev.get("at", 0) < 86400
    tries = {version: {"count": (prev.get("count", 0) if fresh else 0) + 1, "at": time.time()}}
    cache["attempts"] = tries
    _save_cache(cache)


def install(release: dict | None = None) -> str:
    """Download and start installing the latest version.

    Returns a short message. On success the caller must quit the app soon after,
    so the installer (Windows) or the swap script (macOS) can replace it.
    """
    method = install_method()
    if release is None:
        release = _load_cache().get("release") or _release_info(_fetch_json(LATEST_API))
    if not is_newer(release.get("version", "")):
        return "You already have the latest version."
    asset = _asset_for(method, release)
    if asset is None:
        raise RuntimeError(manual_instructions())
    _note_attempt(release["version"])
    work = Path(tempfile.mkdtemp(prefix="ai-token-tracker-update-"))
    if method == "windows-installer":
        setup = work / WINDOWS_ASSET
        _download(asset, setup)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # /VERYSILENT shows no window; the installer waits for this app to quit, then restarts it.
        subprocess.Popen([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=flags, close_fds=True)
        return f"Installing version {release['version']}. The app will restart in a moment."
    if method == "mac-app":
        bundle = _mac_bundle(Path(sys.executable).resolve())
        archive = work / MAC_ASSET
        _download(asset, archive)
        unpacked = work / "new"
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(unpacked)], check=True)
        new_app = next(unpacked.glob("*.app"), None)
        if new_app is None:
            raise RuntimeError("the downloaded update doesn't contain the app")
        script = work / "swap.sh"
        script.write_text(
            "#!/bin/sh\n"
            f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done\n'
            'rm -rf "$1.old" && mv "$1" "$1.old" && mv "$2" "$1" && rm -rf "$1.old"\n'
            'open "$1"\n', encoding="utf-8")
        subprocess.Popen(["/bin/sh", str(script), str(bundle), str(new_app)], start_new_session=True, close_fds=True)
        return f"Installing version {release['version']}. The app will reopen in a moment."
    if method == "pip":
        wheel = work / Path(asset["url"]).name
        _download(asset, wheel)
        extras = "[app]" if _has_app_extra() else ""
        done = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
                               f"{wheel}{extras}"], capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError("pip couldn't install the update: " + (done.stderr or done.stdout).strip()[-400:])
        # Start the new version; this one quits right after.
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # Wait a few seconds first, so this copy has quit and the new one gets the same address.
        relaunch = "import time, runpy; time.sleep(4); runpy.run_module('ai_token_tracker', run_name='__main__')"
        subprocess.Popen([sys.executable, "-c", relaunch], creationflags=flags, close_fds=True,
                         start_new_session=sys.platform != "win32")
        return f"Updated to version {release['version']}. Restarting…"
    shutil.rmtree(work, ignore_errors=True)
    raise RuntimeError(manual_instructions())


def _has_app_extra() -> bool:
    """Whether the native-window extra (pywebview) is installed, so the upgrade keeps it."""
    return importlib.util.find_spec("webview") is not None
