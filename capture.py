"""Multi-monitor screenshot capture for Linux (Wayland/KDE/X11), macOS, and Windows."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

IS_DARWIN = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

Rect = Tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class MonitorInfo:
    """One connected display, numbered from 1 for user-facing commands."""

    index: int
    name: str
    grim_output: Optional[str] = None
    rect: Optional[Rect] = None

    @property
    def geometry_label(self) -> str:
        if not self.rect:
            return ""
        x, y, w, h = self.rect
        return f"{w}x{h} @ {x},{y}"


@dataclass(frozen=True)
class ScreenCapture:
    """One monitor (or desktop) screenshot."""

    name: str
    path: Path
    geometry: Optional[Rect] = None
    monitor_index: Optional[int] = None


def _run(
    cmd: Sequence[str],
    *,
    timeout: float = 30.0,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged,
        check=False,
    )


def _safe_name(raw: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", raw.strip())
    return cleaned or "monitor"


def _parse_kscreen_outputs(text: str) -> List[Tuple[str, Rect]]:
    """Parse ``kscreen-doctor -o`` style output into (name, rect) pairs."""
    regions: List[Tuple[str, Rect]] = []
    current_name: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m_out = re.match(r"^Output\s+(\d+)\s*:", line, re.IGNORECASE)
        if m_out:
            current_name = f"output-{m_out.group(1)}"
            continue
        m_name = re.match(r"^(.+?)\s*:\s*$", line)
        if m_name and "geometry" not in line.lower() and "connected" not in line.lower():
            candidate = m_name.group(1).strip()
            if candidate and not candidate.startswith("Output"):
                current_name = candidate
            continue
        m_geo = re.search(
            r"geometry\s+(-?\d+)\s*,\s*(-?\d+)\s+(\d+)\s*x\s*(\d+)",
            line,
            re.IGNORECASE,
        )
        if m_geo:
            rect = (
                int(m_geo.group(1)),
                int(m_geo.group(2)),
                int(m_geo.group(3)),
                int(m_geo.group(4)),
            )
            name = current_name or f"output-{len(regions)}"
            regions.append((name, rect))
            current_name = None
    return regions


def _kde_output_regions() -> List[Tuple[str, Rect]]:
    if not shutil.which("kscreen-doctor"):
        return []
    proc = _run(["kscreen-doctor", "-o"], timeout=15.0)
    if proc.returncode != 0 and not proc.stdout.strip():
        return []
    return _parse_kscreen_outputs(proc.stdout + "\n" + proc.stderr)


def _kwin_output_config() -> List[MonitorInfo]:
    """Read monitor layout from KDE's persisted KWin output config."""
    path = Path.home() / ".config" / "kwinoutputconfig.json"
    if not path.is_file():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("screen-awareness: kwinoutputconfig parse failed: %s", exc)
        return []

    outputs_block: List[dict] = []
    setups_block: List[dict] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        data = entry.get("data")
        if name == "outputs" and isinstance(data, list):
            outputs_block = data
        elif name == "setups" and isinstance(data, list):
            setups_block = data

    if not outputs_block or not setups_block:
        return []

    active_setup = setups_block[0] if setups_block else {}
    layout_outputs = active_setup.get("outputs") if isinstance(active_setup, dict) else None
    if not isinstance(layout_outputs, list):
        return []

    indexed: List[Tuple[int, dict, dict]] = []
    for layout in layout_outputs:
        if not isinstance(layout, dict) or not layout.get("enabled", True):
            continue
        out_idx = layout.get("outputIndex")
        if not isinstance(out_idx, int) or out_idx < 0 or out_idx >= len(outputs_block):
            continue
        output = outputs_block[out_idx]
        if not isinstance(output, dict):
            continue
        priority = layout.get("priority", out_idx + 1)
        indexed.append((int(priority), layout, output))

    indexed.sort(key=lambda item: (item[0], item[2].get("connectorName", "")))

    monitors: List[MonitorInfo] = []
    for display_index, (_priority, layout, output) in enumerate(indexed, start=1):
        connector = str(output.get("connectorName") or f"output-{display_index}")
        mode = output.get("mode") if isinstance(output.get("mode"), dict) else {}
        native_w = int(mode.get("width") or 0)
        native_h = int(mode.get("height") or 0)
        scale = float(output.get("scale") or 1.0) or 1.0
        pos = layout.get("position") if isinstance(layout.get("position"), dict) else {}
        x = int(pos.get("x") or 0)
        y = int(pos.get("y") or 0)

        if native_w > 0 and native_h > 0:
            width = max(1, round(native_w / scale))
            height = max(1, round(native_h / scale))
            rect: Optional[Rect] = (x, y, width, height)
        else:
            rect = None

        monitors.append(
            MonitorInfo(
                index=display_index,
                name=connector,
                grim_output=connector,
                rect=rect,
            )
        )

    return monitors


def discover_monitors() -> List[MonitorInfo]:
    if IS_DARWIN or IS_WINDOWS:
        from . import capture_platform as cp

        raw = cp.discover_monitors_platform()
        return [
            MonitorInfo(
                index=int(item["index"]),
                name=str(item["name"]),
                rect=item.get("rect"),
            )
            for item in raw
        ]
    return _discover_monitors_linux()


def _discover_monitors_linux() -> List[MonitorInfo]:
    """Return connected monitors in stable 1-based order (Linux)."""
    kscreen_regions = _kde_output_regions()
    if kscreen_regions:
        return [
            MonitorInfo(
                index=idx,
                name=name,
                grim_output=name,
                rect=rect,
            )
            for idx, (name, rect) in enumerate(kscreen_regions, start=1)
        ]

    kwin = _kwin_output_config()
    if kwin:
        return kwin

    return []


def _grim_capture_output(output_name: str, dest: Path) -> bool:
    if not shutil.which("grim"):
        return False
    proc = _run(["grim", "-o", output_name, str(dest)], timeout=20.0)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _grim_capture_geometry(rect: Rect, dest: Path) -> bool:
    if not shutil.which("grim"):
        return False
    x, y, w, h = rect
    region = f"{x},{y} {w}x{h}"
    proc = _run(["grim", "-g", region, str(dest)], timeout=20.0)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _is_kde_wayland() -> bool:
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        return False
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
    if "KDE" in desktop or os.environ.get("KDE_SESSION_VERSION"):
        return True
    return bool(shutil.which("kwin_wayland"))


def _is_wlroots_compositor() -> bool:
    return bool(
        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        or os.environ.get("SWAYSOCK")
        or os.environ.get("WAYLAND_DISPLAY", "").startswith("sway")
    )


def _desktop_logical_bounds(monitors: Sequence[MonitorInfo]) -> Tuple[int, int]:
    max_x = 0
    max_y = 0
    for monitor in monitors:
        if not monitor.rect:
            continue
        x, y, w, h = monitor.rect
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)
    return max_x, max_y


def _crop_monitor_from_desktop(
    desktop: Path,
    monitor: MonitorInfo,
    dest: Path,
    logical_size: Tuple[int, int],
) -> bool:
    if not monitor.rect:
        return False
    x, y, w, h = monitor.rect
    try:
        from PIL import Image

        with Image.open(desktop) as img:
            lw, lh = logical_size
            if lw <= 0 or lh <= 0:
                lw, lh = img.size
            sx = img.width / lw
            sy = img.height / lh
            box = (
                max(0, round(x * sx)),
                max(0, round(y * sy)),
                min(img.width, round((x + w) * sx)),
                min(img.height, round((y + h) * sy)),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                return False
            cropped = img.crop(box)
            cropped.save(dest, format="PNG")
        return dest.is_file() and dest.stat().st_size > 0
    except ImportError:
        logger.warning("screen-awareness: PIL required for KDE monitor crop")
    except Exception as exc:
        logger.debug("screen-awareness: crop failed for %s: %s", monitor.name, exc)
    return False


def _kde_batch_capture(
    monitors: Sequence[MonitorInfo],
    cache_dir: Path,
    stamp: str,
) -> List[ScreenCapture]:
    """Silent full-desktop capture via Spectacle, then crop per monitor."""
    if not monitors or not shutil.which("spectacle"):
        return []
    full = cache_dir / f"{stamp}_desktop_full.png"
    if not _spectacle_fullscreen(full):
        return []
    logical_size = _desktop_logical_bounds(monitors)
    shots: List[ScreenCapture] = []
    for monitor in monitors:
        dest = cache_dir / f"{stamp}_{monitor.index}_{_safe_name(monitor.name)}.png"
        if _crop_monitor_from_desktop(full, monitor, dest, logical_size):
            shots.append(
                ScreenCapture(
                    name=monitor.name,
                    path=dest,
                    geometry=monitor.rect,
                    monitor_index=monitor.index,
                )
            )
    try:
        full.unlink(missing_ok=True)
    except OSError:
        pass
    return shots


def _spectacle_fullscreen(dest: Path) -> bool:
    if not shutil.which("spectacle"):
        return False
    variants = [
        ["spectacle", "--background", "--nonotify", "--fullscreen", "-o", str(dest)],
        ["spectacle", "-b", "-n", "-f", "-o", str(dest)],
    ]
    for cmd in variants:
        proc = _run(cmd, timeout=25.0)
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    return False


def _import_root(dest: Path) -> bool:
    if not shutil.which("import"):
        return False
    display = os.environ.get("DISPLAY")
    if not display:
        return False
    proc = _run(
        ["import", "-window", "root", str(dest)],
        timeout=20.0,
        env={"DISPLAY": display},
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _capture_monitor(monitor: MonitorInfo, dest: Path) -> bool:
    """Per-monitor capture for wlroots compositors (grim). Never opens Spectacle UI."""
    if not _is_wlroots_compositor():
        return False
    if monitor.grim_output and _grim_capture_output(monitor.grim_output, dest):
        return True
    if monitor.rect and _grim_capture_geometry(monitor.rect, dest):
        return True
    return False


def _capture_desktop_fallback(dest: Path) -> bool:
    if _is_kde_wayland():
        if _spectacle_fullscreen(dest):
            return True
    elif shutil.which("grim"):
        proc = _run(["grim", str(dest)], timeout=20.0)
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True
    for attempt in (_spectacle_fullscreen, _import_root):
        try:
            if attempt(dest):
                return True
        except Exception as exc:
            logger.debug("screen-awareness desktop capture via %s failed: %s", attempt.__name__, exc)
    return False


def _normalize_capture_target(target: Optional[str]) -> Optional[int]:
    """Return 1-based monitor index, or None for all monitors."""
    if target is None:
        return None
    raw = str(target).strip().lower()
    if raw in {"", "all", "multi", "multiscreen", "multimonitor", "monitors"}:
        return None
    if raw.isdigit():
        index = int(raw)
        return index if index > 0 else None
    return None


def capture_screens(
    cache_dir: Path,
    *,
    target: Optional[str] = None,
) -> List[ScreenCapture]:
    """Capture all monitors or a single numbered screen (1-based).

    ``target``:
      - ``None`` / ``all`` — every monitor individually
      - ``1``, ``2``, … — only that monitor
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    wanted = _normalize_capture_target(target)

    if IS_DARWIN or IS_WINDOWS:
        from . import capture_platform as cp

        raw = cp.capture_platform(cache_dir, target=wanted)
        if raw:
            return [
                ScreenCapture(
                    name=str(item["name"]),
                    path=Path(item["path"]),
                    monitor_index=item.get("monitor_index"),
                )
                for item in raw
            ]
        if wanted is not None:
            raise ValueError(f"Screen {wanted} could not be captured on this system.")
        return []

    monitors = discover_monitors()
    if monitors:
        selected = monitors if wanted is None else [m for m in monitors if m.index == wanted]
        if wanted is not None and not selected:
            raise ValueError(
                f"Screen {wanted} not found — {len(monitors)} monitor(s) available "
                f"(use /screen-awareness screens to list)."
            )

        if _is_kde_wayland() and shutil.which("spectacle"):
            kde_shots = _kde_batch_capture(selected, cache_dir, stamp)
            if kde_shots:
                logger.debug(
                    "screen-awareness: captured %s monitor(s) via silent Spectacle crop%s",
                    len(kde_shots),
                    f" (screen {wanted})" if wanted is not None else "",
                )
                return kde_shots
            if wanted is not None:
                raise ValueError(
                    f"Screen {wanted} could not be captured silently on KDE Wayland."
                )

        shots: List[ScreenCapture] = []
        for monitor in selected:
            dest = cache_dir / f"{stamp}_{monitor.index}_{_safe_name(monitor.name)}.png"
            if _capture_monitor(monitor, dest):
                shots.append(
                    ScreenCapture(
                        name=monitor.name,
                        path=dest,
                        geometry=monitor.rect,
                        monitor_index=monitor.index,
                    )
                )
            elif wanted is not None:
                raise ValueError(
                    f"Screen {monitor.index} ({monitor.name}) could not be captured."
                )

        if shots:
            logger.debug(
                "screen-awareness: captured %s monitor(s)%s",
                len(shots),
                f" (screen {wanted})" if wanted is not None else "",
            )
            return shots

        if wanted is None:
            logger.warning(
                "screen-awareness: discovered %s monitor(s) but capture failed for all",
                len(monitors),
            )

    # No per-monitor discovery — fall back to one combined desktop image.
    if wanted is not None and wanted != 1:
        raise ValueError(
            f"Screen {wanted} not available — only a single combined desktop "
            "capture is possible on this system."
        )

    fallback = cache_dir / f"{stamp}_desktop.png"
    if _capture_desktop_fallback(fallback):
        logger.debug("screen-awareness: captured combined desktop fallback")
        return [
            ScreenCapture(
                name="desktop",
                path=fallback,
                monitor_index=1 if wanted == 1 else None,
            )
        ]

    return []


def capture_all_monitors(cache_dir: Path) -> List[ScreenCapture]:
    """Backward-compatible alias for multi-monitor capture."""
    return capture_screens(cache_dir, target=None)
