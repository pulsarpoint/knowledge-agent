#!/usr/bin/env python3
"""Supply a transient credential to Git without embedding it in a remote URL."""

import os
import sys

prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if prompt.startswith("username"):
    print(os.environ.get("KA_GIT_USERNAME", "x-access-token"))
elif prompt.startswith("password"):
    print(os.environ.get("KA_GITHUB_TOKEN", ""))
