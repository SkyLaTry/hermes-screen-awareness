# Install

Requires [Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.15.1+.

`install.sh` installs **hermes-essentials** automatically if it is missing.

**One command:**

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

See [README.md](README.md).
