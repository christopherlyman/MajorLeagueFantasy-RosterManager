# 1_Project_Structure

## Purpose
This document defines the intended folder layout for the MajorLeagueFantasy-RosterManager project and separates source code from generated runtime artifacts.

## Source-of-truth structure

### Top level
- `docs/` — project documentation and handoff notes
- `runtime/` — orchestration scripts, compose, status/log folders
- `scripts/` — executable ingestion/build scripts
- `services/` — reusable Python logic for DB access, queries, scoring, league profiles
- `pages/` — Streamlit page modules
- `streamlit_app.py` — primary Streamlit entrypoint
- `.env` — local runtime configuration (not for Git)
- `Dockerfile`, `requirements.txt`, `LICENSE` — project metadata/build inputs

### Scripts layout
- `scripts/yahoo/` — Yahoo API acquisition scripts
- `scripts/` root — MLB/splits/roster pipeline scripts that are project-local but not Yahoo-auth specific

### Data layout
- `data/raw/` — generated raw source pulls
- `data/derived/` — generated transformed outputs
- Generated data is not source code and should not be hand-edited.

### Runtime layout
- `runtime/docker-compose.yml` — local container orchestration
- `runtime/refresh_*.sh` — refresh wrappers
- `runtime/logs/` — generated logs
- `runtime/status/` — generated status files

## Cleanup rules
- No `*.bak` files in the repo tree
- No `__pycache__` or `*.pyc`
- No seam/test files such as `*SEAM1_TEST*` or `*.rmt_test.csv`
- No ad hoc duplicate script trees
- Prefer one approved path per concern

## Naming rules
- Use `scripts/yahoo/`, not temporary names like `scripts/rmt_yahoo/`
- Keep generated files under `data/` or `runtime/`, never beside source unless explicitly required

## Current cleanup intent
1. Remove safe artifacts and backup files
2. Rename `scripts/rmt_yahoo/` to `scripts/yahoo/`
3. Update direct path references
4. Harden `.gitignore`
5. Re-evaluate any ambiguous top-level folders after proof of use
