"""Screen awareness plugin: /screen-awareness on|off + per-API-call monitor capture."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .capture import MonitorInfo, ScreenCapture, capture_screens, discover_monitors
from .deps import install_deps, status_lines as dep_status_lines
from .resize import get_resize_settings, normalize_captures

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".hermes" / "cache" / "screen-awareness"
_MARKER_START = "<!-- hermes-screen-awareness -->"
_MARKER_END = "<!-- /hermes-screen-awareness -->"

_HELP = """\
Automatic screen awareness for the agent

  /screen-awareness on              — enable captures before every API call
  /screen-awareness on install      — install deps, then enable
  /screen-awareness off             — disable
  /screen-awareness install         — install capture/resize deps (Hermes env + pacman hints)
  /screen-awareness                 — status, settings, and monitor list
  /screen-awareness all             — capture every monitor (multiscreen mode)
  /screen-awareness screen 1        — capture only monitor 1
  /screen-awareness 2               — shorthand for screen 2
  /screen-awareness screens         — list numbered monitors

While ON, Hermes captures the selected screen(s) before each agent run and
sub-run API request, then injects them into the current user turn using native
vision when supported, or vision_analyze text descriptions otherwise.

Screenshots are ephemeral (not saved to session history) and cached under:
  ~/.hermes/cache/screen-awareness/

Before injection, captures larger than screen_awareness.max_dimension (default
1920px on the long edge) are downscaled and saved as JPEG. Configure in
~/.hermes/config.yaml under screen_awareness.

Gateway note: captures the display on the machine running the gateway
(same host as Hermes). On a remote VPS this is the server screen, not your PC.
"""


def _load_screen_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        block = load_config().get("screen_awareness", {})
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def _cache_ttl_hours() -> float:
    cfg = _load_screen_config()
    try:
        return float(cfg.get("cache_ttl_hours", 24.0))
    except (TypeError, ValueError):
        return 24.0


def _vision_analyze_fallback_enabled() -> bool:
    cfg = _load_screen_config()
    if "use_vision_analyze_fallback" in cfg:
        return bool(cfg.get("use_vision_analyze_fallback"))
    return True


def _prune_cache(cache_dir: Path) -> None:
    ttl = _cache_ttl_hours()
    if ttl <= 0:
        return
    cutoff = time.time() - (ttl * 3600.0)
    try:
        for path in cache_dir.glob("*"):
            if not path.is_file():
                continue
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("screen-awareness: cache prune skipped: %s", exc)


def is_enabled() -> bool:
    return bool(_load_screen_config().get("enabled", False))


def get_capture_target() -> Optional[str]:
    """Return config capture target: None/'all' for multiscreen, or '1','2',..."""
    raw = _load_screen_config().get("capture", "all")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"", "all", "multi", "multiscreen", "multimonitor"}:
        return None
    if text.isdigit() and int(text) > 0:
        return text
    return None


def capture_mode_label() -> str:
    target = get_capture_target()
    if target is None:
        return "all monitors (multiscreen)"
    return f"screen {target} only"


def _save_config_value(key: str, value: Any) -> bool:
    try:
        from cli import save_config_value

        return bool(save_config_value(f"screen_awareness.{key}", value))
    except Exception as exc:
        logger.warning("screen-awareness: failed to save %s: %s", key, exc)
        return False


def _set_enabled(value: bool) -> bool:
    return _save_config_value("enabled", value)


def _set_capture(target: Optional[str]) -> Tuple[bool, str]:
    if target is None:
        saved = _save_config_value("capture", "all")
        return saved, "all monitors (multiscreen)"
    if not target.isdigit() or int(target) <= 0:
        return False, f"invalid screen number: {target!r}"

    monitors = discover_monitors()
    index = int(target)
    if monitors and index > len(monitors):
        names = ", ".join(str(m.index) for m in monitors)
        return False, (
            f"screen {index} not found — only monitor(s) {names} available "
            f"(use /screen-awareness screens)."
        )

    saved = _save_config_value("capture", target)
    label = f"screen {index} only"
    if monitors:
        match = next((m for m in monitors if m.index == index), None)
        if match and match.geometry_label:
            label = f"screen {index} ({match.name}, {match.geometry_label})"
        elif match:
            label = f"screen {index} ({match.name})"
    return saved, label


def _format_monitor_list(monitors: List[MonitorInfo]) -> str:
    if not monitors:
        return "  (no individual monitors detected — fallback is one combined desktop image)"
    lines = []
    for monitor in monitors:
        geo = f" — {monitor.geometry_label}" if monitor.geometry_label else ""
        lines.append(f"  {monitor.index}. {monitor.name}{geo}")
    return "\n".join(lines)


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


def _strip_previous_block(text: str) -> str:
    pattern = re.compile(
        re.escape(_MARKER_START) + r".*?" + re.escape(_MARKER_END),
        re.DOTALL,
    )
    cleaned = pattern.sub("", text).strip()
    return cleaned


def _format_header(
    shots: List[ScreenCapture],
    *,
    api_call_count: int,
    capture_mode: str,
) -> str:
    lines = [
        _MARKER_START,
        f"[Screen awareness active — {capture_mode}, API call #{api_call_count}, "
        f"{len(shots)} screenshot(s) attached.]",
    ]
    for shot in shots:
        geo = ""
        if shot.geometry:
            x, y, w, h = shot.geometry
            geo = f" @ {x},{y} {w}x{h}"
        index = ""
        if shot.monitor_index is not None:
            index = f" [screen {shot.monitor_index}]"
        lines.append(f"  • {shot.name}{index}{geo}: {shot.path}")
    lines.append(_MARKER_END)
    return "\n".join(lines)


def _decide_image_mode(provider: str, model: str) -> str:
    try:
        from agent.image_routing import decide_image_input_mode
        from hermes_cli.config import load_config

        return decide_image_input_mode(
            (provider or "").strip(),
            (model or "").strip(),
            load_config(),
        )
    except Exception as exc:
        logger.debug("screen-awareness: image mode decision failed: %s", exc)
        return "text"


def _analyze_images(image_paths: List[str]) -> List[str]:
    from tools.vision_tools import vision_analyze_tool

    prompt = (
        "Describe everything visible on this monitor screenshot in thorough detail. "
        "Include windows, text, code, UI elements, colors, layout, and anything "
        "the user might be working on."
    )
    blocks: List[str] = []

    async def _run_all() -> List[str]:
        results: List[str] = []
        for raw_path in image_paths:
            path = Path(raw_path)
            if not path.is_file():
                continue
            label = path.stem.replace("_", " ")
            try:
                result_json = await vision_analyze_tool(
                    image_url=str(path),
                    user_prompt=prompt,
                )
                result = json.loads(result_json)
                if result.get("success"):
                    analysis = (result.get("analysis") or "").strip()
                    results.append(
                        f"[Screen capture ({label}):\n{analysis}]\n"
                        f"[Re-examine with vision_analyze using image_url: {path}]"
                    )
                else:
                    results.append(
                        f"[Screen capture ({label}) could not be analyzed. "
                        f"Try vision_analyze with image_url: {path}]"
                    )
            except Exception as exc:
                results.append(
                    f"[Screen capture ({label}) analysis failed ({exc}). "
                    f"Try vision_analyze with image_url: {path}]"
                )
        return results

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            blocks = pool.submit(lambda: asyncio.run(_run_all())).result()
    else:
        blocks = asyncio.run(_run_all())

    return blocks


def _find_current_user_message(
    request_messages: List[Any],
) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    for idx in range(len(request_messages) - 1, -1, -1):
        msg = request_messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "user":
            return idx, msg
    return None, None


def _inject_screen_context(
    msg: Dict[str, Any],
    *,
    shots: List[ScreenCapture],
    provider: str,
    model: str,
    api_call_count: int,
    capture_mode: str,
) -> None:
    image_paths = [str(s.path) for s in shots]
    base_text = _strip_previous_block(_extract_text_content(msg.get("content")))
    header = _format_header(
        shots,
        api_call_count=api_call_count,
        capture_mode=capture_mode,
    )
    mode = _decide_image_mode(provider, model)

    if mode == "native":
        try:
            from agent.image_routing import build_native_content_parts

            combined_text = f"{base_text}\n\n{header}".strip() if base_text else header
            parts, skipped = build_native_content_parts(combined_text, image_paths)
            if any(p.get("type") == "image_url" for p in parts):
                msg["content"] = parts
                if skipped:
                    logger.warning(
                        "screen-awareness: skipped unreadable image(s): %s",
                        ", ".join(skipped),
                    )
                return
        except Exception as exc:
            logger.warning("screen-awareness: native attach failed, using text mode: %s", exc)

    analysis_blocks = _analyze_images(image_paths) if _vision_analyze_fallback_enabled() else []
    if not analysis_blocks and mode != "native":
        analysis_blocks = [
            "[Screen capture attached but vision_analyze fallback is disabled "
            "(screen_awareness.use_vision_analyze_fallback: false). "
            "Enable native vision or set use_vision_analyze_fallback: true.]"
        ]
    injection_parts = [header] + analysis_blocks
    injection = "\n\n".join(injection_parts)
    msg["content"] = f"{base_text}\n\n{injection}".strip() if base_text else injection


def on_pre_api_request(**kwargs: Any) -> None:
    """Capture monitors and inject into the current turn's user message."""
    if not is_enabled():
        return
    from .deps import ensure_hook_ready

    if not ensure_hook_ready():
        logger.warning("screen-awareness: hook skipped — dependencies missing")
        request_messages = kwargs.get("request_messages")
        if isinstance(request_messages, list) and request_messages:
            _, user_msg = _find_current_user_message(request_messages)
            if user_msg is not None:
                from .deps import status_lines

                hint = "\n".join(status_lines()[:6])
                user_msg["content"] = (
                    _extract_text_content(user_msg.get("content"))
                    + f"\n\n{_MARKER_START}\n[Screen awareness: enabled but capture tools missing.\n"
                    f"Install deps or run /screen-awareness off.\n{hint}]\n{_MARKER_END}"
                ).strip()
        return

    request_messages = kwargs.get("request_messages")
    if not isinstance(request_messages, list) or not request_messages:
        return

    _, user_msg = _find_current_user_message(request_messages)
    if user_msg is None:
        return

    capture_target = get_capture_target()
    capture_mode = capture_mode_label()
    _prune_cache(_CACHE_DIR)

    try:
        shots = normalize_captures(
            capture_screens(_CACHE_DIR, target=capture_target)
        )
    except ValueError as exc:
        user_msg["content"] = (
            _extract_text_content(user_msg.get("content"))
            + f"\n\n{_MARKER_START}\n[Screen awareness: {exc}]\n{_MARKER_END}"
        ).strip()
        return
    except Exception as exc:
        logger.warning("screen-awareness: capture failed: %s", exc)
        user_msg["content"] = (
            _extract_text_content(user_msg.get("content"))
            + f"\n\n{_MARKER_START}\n[Screen awareness: capture failed — {exc}]\n{_MARKER_END}"
        ).strip()
        return

    if not shots:
        logger.warning("screen-awareness: no monitors captured")
        user_msg["content"] = (
            _extract_text_content(user_msg.get("content"))
            + f"\n\n{_MARKER_START}\n[Screen awareness: no monitors captured]\n{_MARKER_END}"
        ).strip()
        return

    _inject_screen_context(
        user_msg,
        shots=shots,
        provider=str(kwargs.get("provider") or ""),
        model=str(kwargs.get("model") or ""),
        api_call_count=int(kwargs.get("api_call_count") or 1),
        capture_mode=capture_mode,
    )


def _status_message() -> str:
    state = "ON" if is_enabled() else "OFF"
    max_dim, quality = get_resize_settings()
    monitors = discover_monitors()
    dep_block = "\n".join(dep_status_lines())
    return (
        f"Screen awareness is {state}.\n"
        f"Capture: {capture_mode_label()}.\n"
        f"Resize: max {max_dim}px long edge, JPEG quality {quality}.\n\n"
        f"{dep_block}\n\n"
        f"Monitors:\n{_format_monitor_list(monitors)}\n\n"
        f"{_HELP}"
    )


def _handle_screen_awareness(raw_args: str) -> str:
    args = (raw_args or "").strip()
    arg = args.lower()

    if not arg:
        return _status_message()

    if arg.split()[0] in {"on", "enable", "true", "yes"} or arg.startswith("on "):

        def _do_enable() -> str:
            saved = _set_enabled(True)
            note = "saved to config" if saved else "session only (config save failed)"
            return f"Screen awareness ON ({note}). Capture: {capture_mode_label()}.\n\n{_HELP}"

        try:
            from .deps import enable_with_deps

            return enable_with_deps(
                args,
                _do_enable,
                action_label="enable screen awareness",
            )
        except Exception:
            from .deps import deps_ready

            if not deps_ready():
                return (
                    "Cannot enable screen awareness — dependencies missing.\n"
                    "Run: /screen-awareness on install\n\n" + _HELP
                )
            return _do_enable()

    if arg in {"off", "disable", "false", "no"}:
        saved = _set_enabled(False)
        note = "saved to config" if saved else "session only (config save failed)"
        return f"Screen awareness OFF ({note}).\n\n{_HELP}"

    if arg == "install" or arg.startswith("install "):
        ok, msg = install_deps(include_optional=False)
        prefix = "Install OK" if ok else "Install failed"
        return f"{prefix}: {msg}\n\n{_HELP}"

    if arg in {"screens", "screen", "monitors", "list"}:
        monitors = discover_monitors()
        current = capture_mode_label()
        return (
            f"Capture mode: {current}.\n\n"
            f"Monitors:\n{_format_monitor_list(monitors)}\n\n"
            "Switch with:\n"
            "  /screen-awareness all\n"
            "  /screen-awareness screen 1\n"
            "  /screen-awareness 2"
        )

    if arg in {"all", "multi", "multiscreen", "multimonitor", "monitors"}:
        saved, label = _set_capture(None)
        note = "saved to config" if saved else "session only (config save failed)"
        return f"Capture mode set to {label} ({note}).\n\n{_HELP}"

    if arg.startswith("mode "):
        arg = arg[5:].strip()

    if arg.startswith("screen "):
        target = arg[7:].strip()
        if not target:
            return "Usage: /screen-awareness screen <number>\n\n" + _HELP
        saved, label = _set_capture(target)
        if not saved and "not found" in label:
            return label + "\n\n" + _HELP
        note = "saved to config" if saved else "session only (config save failed)"
        return f"Capture mode set to {label} ({note}).\n\n{_HELP}"

    if arg.isdigit():
        saved, label = _set_capture(arg)
        if not saved and "not found" in label:
            return label + "\n\n" + _HELP
        note = "saved to config" if saved else "session only (config save failed)"
        return f"Capture mode set to {label} ({note}).\n\n{_HELP}"

    return f"Unknown argument: {raw_args!r}\n\n{_HELP}"


def register(ctx) -> None:
    ctx.register_command(
        "screen-awareness",
        handler=_handle_screen_awareness,
        description="Toggle screen captures and choose all monitors vs a single screen.",
        args_hint="on|off|install|all|screen N|screens",
    )
    ctx.register_hook("pre_api_request", on_pre_api_request)
