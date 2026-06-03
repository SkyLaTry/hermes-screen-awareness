"""macOS / Windows screen capture helpers for screen-awareness."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

IS_DARWIN = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"


def platform_capture_available() -> bool:
    if IS_DARWIN:
        import shutil

        return shutil.which("screencapture") is not None
    if IS_WINDOWS:
        import shutil

        return shutil.which("powershell") is not None
    return False


def discover_monitors_platform() -> List[dict]:
    if IS_DARWIN:
        return [{"index": 1, "name": "display-1", "rect": None}]
    if IS_WINDOWS:
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$i=1; [System.Windows.Forms.Screen]::AllScreens | ForEach-Object { "
                    "$b=$_.Bounds; Write-Output (\"$i|\" + $_.DeviceName + \"|\" + $b.X + \"|\" + $b.Y + \"|\" + $b.Width + \"|\" + $b.Height); $i++ }",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            out: List[dict] = []
            for line in (proc.stdout or "").splitlines():
                parts = line.strip().split("|")
                if len(parts) < 6:
                    continue
                out.append(
                    {
                        "index": int(parts[0]),
                        "name": parts[1],
                        "rect": (
                            int(parts[2]),
                            int(parts[3]),
                            int(parts[4]),
                            int(parts[5]),
                        ),
                    }
                )
            if out:
                return out
        except Exception as exc:
            logger.debug("screen-awareness: Windows monitor discovery failed: %s", exc)
        return [{"index": 1, "name": "primary", "rect": None}]
    return []


def capture_platform(
    cache_dir: Path,
    *,
    target: Optional[int] = None,
) -> List[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    if IS_DARWIN:
        dest = cache_dir / f"{stamp}_display_{target or 1}.png"
        cmd = ["screencapture", "-x"]
        if target is not None and target > 0:
            cmd.extend(["-D", str(target)])
        cmd.append(str(dest))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode == 0 and dest.is_file():
            return [{"name": f"display-{target or 1}", "path": dest, "monitor_index": target or 1}]
        return []
    if IS_WINDOWS:
        index = max(1, int(target or 1))
        dest = cache_dir / f"{stamp}_display_{index}.png"
        ps = f"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$screens = [System.Windows.Forms.Screen]::AllScreens
$idx = {index - 1}
if ($idx -ge $screens.Length) {{ exit 2 }}
$b = $screens[$idx].Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [Drawing.Point]::Empty, $b.Size)
$bmp.Save("{dest}", [System.Drawing.Imaging.ImageFormat]::Png)
"""
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode == 0 and dest.is_file():
            return [{"name": f"display-{index}", "path": dest, "monitor_index": index}]
        return []
    return []
