#!/usr/bin/env bash
set -euo pipefail

project_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python_executable="$project_dir/.venv/bin/python"

if [[ ! -x "$python_executable" ]]; then
    printf 'The application virtual environment is missing: %s\nSee README.md for installation instructions.\n' "$python_executable" >&2
    exit 1
fi

cd -- "$project_dir"
exec "$python_executable" -m song_metadata_enricher "$@"
