#!/bin/bash
echo "Sudo is required to run this script."
echo "If you have .venv installed, run this script from the root of the project."
sudo "$(pwd)/.venv/bin/python" "$(pwd)/main.py" "$@"
echo "Done."