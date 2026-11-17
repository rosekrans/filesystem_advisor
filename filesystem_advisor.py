#!/usr/bin/env python3
"""
Filesystem Cleanup Advisor
--------------------------
Uses a local Ollama model (default: phi3:3.8b) to analyze filesystem data
and provide careful cleanup advice.

Core idea:
  You feed data → model analyzes it → everything is saved to history.
  Interactive chatting is optional (only if you pass --chat).

Features:
- Gathers cache/directory size summaries and package information
- Can also accept external data files (--data)
- Saves every run (prompt + model response + snapshot) into history
- Can continue from previous sessions so context is remembered
- NEVER deletes anything — only advises

Requirements:
    pip install requests

Typical usage (non-interactive):
    python filesystem_advisor.py                     # new scan + analysis
    python filesystem_advisor.py --continue          # new scan, keep previous context
    python filesystem_advisor.py --data mydata.json  # feed external data
    python filesystem_advisor.py --chat              # enter interactive mode after analysis
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "phi3:3.8b"
OLLAMA_URL = "http://localhost:11434/api/chat"
HISTORY_DIR = Path("fs_advisor_history")
MAX_DEPTH = 2
TOP_N_LARGE = 15

DEFAULT_SCAN_PATHS = [
    "~/.cache",
    "~/.local/share",
    "/tmp",
    "~/Library/Caches",          # macOS
    "~/.npm",
    "~/.cache/pip",
    "~/.cache/huggingface",
    "~/.cache/torch",
    "~/.cache/yarn",
    "~/.gradle/caches",
    "~/.m2/repository",
]

# ... (full content continues identically to the local file) ...

if __name__ == "__main__":
    main()
