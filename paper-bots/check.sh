#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
(cd "$root/moving" && python3 -m unittest -v test_moving)
(cd "$root/shift" && python3 accepted_range_paper.py --self-test)
(cd "$root/ndax" && python3 -m unittest -v test_movable_window)
