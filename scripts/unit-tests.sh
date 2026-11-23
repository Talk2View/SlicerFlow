#!/usr/bin/env bash
# The tests that need no 3D Slicer (tests/), with the Talk2View SDK release the module pins
# (SDK_VERSION in SlicerFlowLib/sdk.py), from PyPI. SF_SDK overrides it with another
# requirement, such as a wheel or a checkout: SF_SDK=~/Documents/talk2view-python
#
#   scripts/unit-tests.sh [pytest args]
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
version="$(sed -n 's/^SDK_VERSION = "\(.*\)"$/\1/p' "$repo/SlicerFlow/SlicerFlowLib/sdk.py")"
sdk="${SF_SDK:-talk2view==$version}"
cd "$repo"
exec uv run --no-project --python 3.12 --with pytest --with "$sdk" python -m pytest tests "$@"
