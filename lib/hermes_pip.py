"""Install Python packages into Hermes venv or ~/.hermes/site-packages — vendored per plugin."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

USER_SITE = Path.home() / ".hermes" / "site-packages"


def resolve_hermes_python() -> str:
    explicit = (os.environ.get("HERMES_PYTHON") or "").strip()
    if explicit:
        return explicit
    exe = Path(sys.executable).resolve()
    if exe.is_file() and (exe.parent.parent / "pyvenv.cfg").is_file():
        return str(exe)
    venv_root = (os.environ.get("VIRTUAL_ENV") or "").strip()
    if venv_root:
        for name in ("python3", "python"):
            candidate = Path(venv_root) / "bin" / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    for candidate in (
        Path("/opt/hermes-agent/venv/bin/python3"),
        Path("/opt/hermes-agent/venv/bin/python"),
        Path.home() / ".hermes/venv/bin/python3",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable or "/usr/bin/python3"


def _default_hermes_site() -> Path:
    py = Path(resolve_hermes_python())
    if py.parent.name == "bin":
        site = py.parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if site.is_dir():
            return site
    for legacy in Path("/opt/hermes-agent/venv/lib").glob("python*/site-packages"):
        if legacy.is_dir():
            return legacy
    return USER_SITE


def target_site() -> Path:
    site = _default_hermes_site()
    if site.is_dir() and os.access(site, os.W_OK):
        return site
    USER_SITE.mkdir(parents=True, exist_ok=True)
    _ensure_user_site_on_path()
    return USER_SITE


def _ensure_user_site_on_path() -> None:
    site = str(USER_SITE)
    if site not in sys.path:
        sys.path.insert(0, site)
    existing = os.environ.get("PYTHONPATH", "")
    if site not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = f"{site}{os.pathsep}{existing}" if existing else site


def hermes_env() -> dict[str, str]:
    _ensure_user_site_on_path()
    env = os.environ.copy()
    site = str(target_site())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{site}{os.pathsep}{existing}" if existing else site
    return env


def install_packages(*specs: str, timeout: int = 600) -> bool:
    if not specs:
        return True
    dest = target_site()
    python = resolve_hermes_python()
    args = ["-U", *specs]
    uv_bin = shutil.which("uv")
    if uv_bin:
        cmd = [uv_bin, "pip", "install", "--python", python, "--target", str(dest), *args]
        if subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).returncode == 0:
            return True
    pip_cmd = [python, "-m", "pip", "install", "--target", str(dest), *args]
    proc = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode == 0


def pkg_installed(module_name: str) -> bool:
    code = f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module_name!r}) else 1)"
    try:
        proc = subprocess.run(
            [resolve_hermes_python(), "-c", code],
            env=hermes_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def verify_imports(modules: Sequence[str]) -> dict[str, bool]:
    return {name: pkg_installed(name) for name in modules}
