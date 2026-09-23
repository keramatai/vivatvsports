# IPTV Default

This repository contains scripts and configurations for managing IPTV playlists, EPG data, and health checks.

## Features

- Automatic EPG fetching via scheduled GitHub Actions
- Health checks for stream validity
- Automated commits to keep data up-to-date

## Workflows

- **health.yml**: Runs health checks every 6 hours and updates `readme.md` with results.
- **epg.yml**: Fetches EPG data twice daily and updates.

## Getting Started

1. Clone the repository.
2. Ensure you have [uv](https://github.com/astral-sh/uv) installed for Python dependency management.
3. Run `uv sync` to install dependencies.
4. Execute `health.sh` or `uv run M3U8/epg-fetch.py` manually if needed.

## License

MIT