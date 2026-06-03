# screen-awareness

**Author:** [SkyLaTry](https://github.com/SkyLaTry) · **Hermes:** 0.15.1+ · **Version:** 1.1.2 · **Repo:** [SkyLaTry/hermes-screen-awareness](https://github.com/SkyLaTry/hermes-screen-awareness)

Captures selected monitor(s) before each agent API call and injects ephemeral vision context into the current turn (native vision or `vision_analyze` fallback).

## Install

Requires [Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.15.1+ and `git`.

**Hermes (once, if not installed):**

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc
```

**This plugin (one command — installs [hermes-essentials](https://github.com/SkyLaTry/hermes-essentials) automatically if missing):**

```bash
curl -fsSL https://raw.githubusercontent.com/SkyLaTry/hermes-screen-awareness/main/install.sh | bash
hermes gateway restart
```

**Alternative (Hermes CLI):**

```bash
hermes plugins install SkyLaTry/hermes-essentials --enable
hermes plugins install SkyLaTry/hermes-screen-awareness --enable
hermes gateway restart
```

```yaml
plugins:
  enabled:
    - hermes-essentials
    - screen-awareness
```

Install capture dependencies: `/screen-awareness install` or `/deps install screen-awareness`.

## Commands

```
/screen-awareness on|off
/screen-awareness all              # all monitors
/screen-awareness screen 2         # single monitor
/screen-awareness install          # deps into Hermes venv
/screen-awareness                  # status + monitor list
```

## Configuration

```yaml
screen_awareness:
  enabled: true
  max_dimension: 1920
  jpeg_quality: 85
  capture: "1"    # monitor index, or "all"
  cache_ttl_hours: 24
  use_vision_analyze_fallback: true   # false = skip vision_analyze when native vision unavailable
```

Cache directory: `~/.hermes/cache/screen-awareness/` (ephemeral, not session history).

## Requirements

- Linux, macOS, or Windows desktop with a supported capture path (see `/screen-awareness install`)
- **hermes-essentials** recommended for `/deps` pip installs into the Hermes venv
- **Optional:** Pillow for KDE multi-monitor crop on Linux

## Gateway note

Captures the display on the **machine running the gateway** — on a remote VPS that is the server screen, not your laptop.

## Related SkyLaTry plugins

See [PLUGINS.md](PLUGINS.md) for install one-liners and the full index.

| Plugin | Repository |
|--------|------------|
| Hermes Essentials | [hermes-essentials](https://github.com/SkyLaTry/hermes-essentials) |
| **Screen Awareness** *(this repo)* | [hermes-screen-awareness](https://github.com/SkyLaTry/hermes-screen-awareness) |
| Sys Controll | [hermes-sys-controll](https://github.com/SkyLaTry/hermes-sys-controll) |
| Lemonade LLM Image | [hermes-lemonade-llm-image-support](https://github.com/SkyLaTry/hermes-lemonade-llm-image-support) |
| Image Local Tools | [hermes-image-local-tools](https://github.com/SkyLaTry/hermes-image-local-tools) |

## License

SkyLaTry Shared Source License — see [LICENSE](LICENSE) and [LICENSING.md](LICENSING.md).
