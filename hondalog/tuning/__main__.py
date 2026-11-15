"""``python -m hondalog.tuning`` runs the hondalog-tune command line."""

import sys

from .cli import main

sys.exit(main())
