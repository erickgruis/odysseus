<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<p align="center">
  A self-hosted AI workspace for chat, agents, research, documents, email, notes, calendar, and local model workflows.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="website/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Quick Start (Linux, system install)

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/odysseus-dev/odysseus/tree/main) if you want the more curated branch.

Requirements: Python 3.11+, `git`, and `tmux` (Cookbook uses tmux for background model downloads and serves). Node.js/npm is optional and only needed for the browser MCP tool.

Arch Linux:

```bash
sudo pacman -S --needed python git tmux
```

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git tmux
```

Clone into `~/odysseus` so the bundled systemd unit works unchanged:

```bash
git clone https://github.com/erickgruis/odysseus.git ~/odysseus
cd ~/odysseus
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
./venv/bin/python setup.py
mkdir -p ~/.config/systemd/user
cp deploy/systemd/odysseus.service ~/.config/systemd/user/odysseus.service
systemctl --user daemon-reload
systemctl --user enable --now odysseus
```

Open `http://127.0.0.1:7011`. `setup.py` creates `.env` from `.env.example` and asks for the first admin password; set `ODYSSEUS_ADMIN_PASSWORD` before running it to preseed one non-interactively.

Useful commands:

```bash
systemctl --user status odysseus
journalctl --user -u odysseus -f
systemctl --user restart odysseus
```

If the service should keep running without an active login session (headless server), enable lingering once:

```bash
sudo loginctl enable-linger "$USER"
```

The unit binds to `127.0.0.1:7011`. Edit `--port` in `~/.config/systemd/user/odysseus.service` to use another port. Keep `AUTH_ENABLED=true` before binding outside loopback.

If the repo lives somewhere other than `~/odysseus`, run `./install-service.sh` instead of copying the unit by hand — it rewrites the `%h/odysseus` paths to the checkout location.

Web search needs SearXNG. To keep the app native and run only SearXNG in Docker:

```bash
cd ~/odysseus
docker compose up -d searxng
```

It binds `127.0.0.1:8080`, which matches the default `SEARXNG_INSTANCE`. Port, image pin, secret, and update notes are in [Optional: SearXNG via Docker](website/setup.md#optional-searxng-via-docker).

Docker, Windows, macOS, HTTPS, and advanced configuration live in the [setup guide](website/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Demo

A full hover-to-play tour lives on the [Odysseus landing page](https://odysseus-dev.github.io/odysseus/). Its source lives under [`website/`](website/).

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.

Deployment details are in the [setup guide](website/setup.md#security-notes).

## Community & Growth

<p align="center">
  <a href="https://trendshift.io/repositories/43167">
      <a href="https://trendshift.io/repositories/43167?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-43167" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/43167/monthly" alt="odysseus-dev%2Fodysseus | Trendshift" width="230"/></a>
  </a>
</p>

<p align="center">
  <a href="https://star-history.dera.page/#odysseus-dev/odysseus&type=date&legend=top-left">
    <picture>
      <source
        media="(prefers-color-scheme: dark)"
        srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&theme=dark&legend=top-left"
      />
      <source
        media="(prefers-color-scheme: light)"
        srcset="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left"
      />
      <img
        src="https://star-history.dera.page/svg?repos=odysseus-dev/odysseus&type=date&legend=top-left"
        alt="Odysseus GitHub star history"
        width="100%"
      />
    </picture>
  </a>
</p>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
