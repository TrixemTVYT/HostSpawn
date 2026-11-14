#!/usr/bin/env python3
"""Entry point (run with python3 -I, which doesn't add this folder to sys.path by itself)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manager.main import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
