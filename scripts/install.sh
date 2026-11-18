#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install 'Flask>=3.1,<4' 'waitress>=3,<4'
.venv/bin/python - <<'PY'
import cv2, numpy, PIL
print('OpenCV:', cv2.__version__)
PY
printf '\nTiếp theo: .venv/bin/python manage.py init\n'
