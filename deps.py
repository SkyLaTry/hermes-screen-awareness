"""screen-awareness dependency checks — self-contained for this plugin."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence, Tuple

_PLUGIN_ID = "screen-awareness"
_OPTIONAL_SPECS = ("pillow>=10.0.0",)
_OPTIONAL_MODULES = ("PIL",)
_CAPTURE_COMMANDS_LINUX = ("grim", "spectacle", "import")


def _load_hermes_pip():
    try:
        from hermes_plugins.hermes_essentials.tts import hermes_pip

        return hermes_pip
    except ImportError:
        path = Path.home() / ".hermes/plugins/hermes-essentials/tts/hermes_pip.py"
        spec = importlib.util.spec_from_file_location("hermes_pip_screen_awareness", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load hermes_pip from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


_hp = _load_hermes_pip()


@dataclass
class DepCheckResult:
    ready: bool
    missing_bins: List[str] = field(default_factory=list)
    missing_optional_python: List[str] = field(default_factory=list)
    available_bins: List[str] = field(default_factory=list)
    install_site: str = ""

    def summary_short(self) -> str:
        return "; ".join(self.missing_bins) or "ok"

    def status_lines(self) -> List[str]:
        lines = [f"{_PLUGIN_ID}:"]
        site = self.install_site or str(_hp.target_site())
        lines.append(f"  Hermes site-packages: {site}")
        for mod in _OPTIONAL_MODULES:
            mark = "ok" if mod not in self.missing_optional_python else "not installed"
            lines.append(f"  python {mod} (optional): {mark}")
        for label in self.missing_bins:
            lines.append(f"  system: {label} — missing")
        for label in self.available_bins:
            lines.append(f"  system: {label} — ok")
        lines.append("  status: ready" if self.ready else f"  status: missing — {self.summary_short()}")
        return lines


def _has_capture_tool() -> bool:
    if sys.platform == "darwin":
        return shutil.which("screencapture") is not None
    if sys.platform == "win32":
        return shutil.which("powershell") is not None
    return any(shutil.which(cmd) for cmd in _CAPTURE_COMMANDS_LINUX)


def _has_resize_backend() -> bool:
    if _hp.pkg_installed("PIL"):
        return True
    return bool(shutil.which("magick") or shutil.which("convert"))


def _check() -> DepCheckResult:
    _hp._ensure_user_site_on_path()
    missing_opt = [m for m in _OPTIONAL_MODULES if not _hp.pkg_installed(m)]
    missing_bins: List[str] = []
    available: List[str] = []
    if _has_capture_tool():
        if sys.platform == "darwin":
            found = "screencapture"
        elif sys.platform == "win32":
            found = "powershell (System.Drawing capture)"
        else:
            found = next(cmd for cmd in _CAPTURE_COMMANDS_LINUX if shutil.which(cmd))
        available.append(f"screen capture ({found})")
    else:
        missing_bins.append(
            "screen capture tool (Linux: grim/spectacle/import; macOS: screencapture; Windows: powershell)"
        )
    if _has_resize_backend():
        available.append("Pillow or ImageMagick resize")
    else:
        missing_bins.append("Pillow or ImageMagick (resize)")
    return DepCheckResult(
        ready=not missing_bins,
        missing_bins=missing_bins,
        missing_optional_python=missing_opt,
        available_bins=available,
        install_site=str(_hp.target_site()),
    )


def deps_ready() -> bool:
    return _check().ready


def install_deps(*, include_optional: bool = False) -> Tuple[bool, str]:
    if include_optional and _OPTIONAL_SPECS:
        dest = _hp.target_site()
        if not _hp.install_packages(*_OPTIONAL_SPECS):
            return False, f"Optional Python install failed into {dest}"
    recheck = _check()
    if not recheck.ready:
        lines = recheck.status_lines()
        lines.append("")
        if sys.platform == "darwin":
            lines.append("macOS: screencapture is built in; optional: pip install pillow")
        elif sys.platform == "win32":
            lines.append("Windows: uses PowerShell + System.Drawing (built in)")
        else:
            lines.append("Linux (Arch): sudo pacman -S --needed grim spectacle imagemagick")
        return False, "\n".join(lines)
    return True, "Dependencies ready"


def status_lines() -> Sequence[str]:
    return _check().status_lines()


def ensure_hook_ready() -> bool:
    """Return True when hook work can run; never auto-installs."""
    if deps_ready():
        _hp._ensure_user_site_on_path()
        return True
    return False


def enable_with_deps(raw_args: str, enable_fn, *, action_label: str = "enable") -> str:
    """Enable only when deps are ready, or after explicit ``install`` confirmation."""
    wants_install = "install" in (raw_args or "").lower().split()
    if wants_install:
        if not deps_ready():
            ok, msg = install_deps(include_optional=False)
            if not ok:
                return f"Install failed ({_PLUGIN_ID}):\n{msg}\n\nNot enabling."
            if not deps_ready():
                return f"Install finished but dependencies still missing.\n{msg}\n\nNot enabling."
        return enable_fn()
    if not deps_ready():
        check = _check()
        return (
            f"Cannot {action_label} — {_PLUGIN_ID} is missing dependencies:\n"
            f"{check.summary_short()}\n\n"
            + "\n".join(check.status_lines())
            + f"\n\nConfirm install + enable: /screen-awareness on install"
        )
    return enable_fn()

