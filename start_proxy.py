#!/usr/bin/env python3
"""ClaudeGate launcher script."""

import os
import sys

# Ensure current directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from src.cli import cli_main

if __name__ == "__main__":
    cli_main()
