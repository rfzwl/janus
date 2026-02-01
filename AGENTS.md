# Repository Guidelines

## Project

- What this project does: A lightweight, distributed multi-account trading console built on vn.py, with a server that manages gateways and a TUI client that connects over RPC.
- Primary goal: Provide a single terminal to monitor accounts and place manual orders across brokers.
- Users: Traders/operators running multiple broker accounts.

## Project Structure & Module Organization

- Source code: `src/janus/`.
- Server entrypoint: `src/janus/server.py` (RPC service, gateway registration, account connect loop).
- Client entrypoint: `src/janus/client.py` (RPC client, command routing).
- TUI: `src/janus/tui.py` (prompt_toolkit + rich).
- Config: `src/janus/config.py` plus `config.yaml` (local) and `config.yaml.example` (template).
- Gateways: `src/janus/gateway/*` (currently Webull in `src/janus/gateway/webull/webull_gateway.py`).

## Tech Stack (only non-obvious)

- Language/runtime: Python >= 3.11 (per `pyproject.toml`).
- Frameworks/libs: vn.py, vnpy_rpcservice, prompt_toolkit, rich, webull-openapi-python-sdk.
- Transport: ZeroMQ RPC via vn.py RPC service.

## Constraints

- Prefer small, atomic changes.
- Ask before touching many files or making broad refactors.

## Code Style & Preferences

- Readability > clever abstractions.
- Prefer explicit types and clear control flow.
- Add comments only for non-obvious logic.
- Avoid refactors unless explicitly requested.

## Build, Test, and Development Commands

- Package manager: **uv** (preferred).
- Install (editable): `uv venv` then `uv pip install -e .`
- Run server: `uv run python -m janus.server`
- Run client: `uv run python -m janus.client`
- Tests: none currently; add tests only when requested or when behavior changes need coverage.

## Config & Secrets

- `config.yaml` contains broker credentials and RPC addresses; do not commit real secrets.
- Use `config.yaml.example` as the template for new setups.

## Git Rules

- Commit only files you actually changed.
- Do not reformat unrelated code.
- If unsure about the blast radius, ask first.
