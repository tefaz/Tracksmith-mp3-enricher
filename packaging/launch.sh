#!/usr/bin/env bash
set -euo pipefail

bundle_dir="$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
python_executable="$bundle_dir/python/bin/python3"
analysis_prefix="${XDG_DATA_HOME:-$HOME/.local/share}/tracksmith/analysis"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$bundle_dir/app:$analysis_prefix/lib/python3.12/site-packages"
export LD_LIBRARY_PATH="$bundle_dir/native-lib:$bundle_dir/python/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

if [[ "${1:-}" == "--install-ai" ]]; then
    "$python_executable" -m pip install --prefix "$analysis_prefix" \
        --index-url https://download.pytorch.org/whl/cpu 'torch>=2.5'
    "$python_executable" -m pip install --prefix "$analysis_prefix" \
        -r "$bundle_dir/requirements-ai.txt"
    printf 'CPU analysis packages installed. Open Tracksmith to download a model and analyze timing.\n'
    exit 0
fi
if [[ "${1:-}" == "--python" ]]; then
    shift
    exec "$python_executable" "$@"
fi
if [[ "${1:-}" == "--version" ]]; then
    exec "$python_executable" -c 'from tracksmith import __version__; print("Tracksmith " + __version__)'
fi
exec "$python_executable" -m tracksmith "$@"
