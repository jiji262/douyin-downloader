"""One-shot: print Douzy transcript.api_key to stdout (caller sets env). Do not commit output."""
import os
import re
import sys
from pathlib import Path

cfg = Path(os.environ.get("DOUZY_CFG") or (Path(os.environ["APPDATA"]) / "Douzy" / "config.yml"))
text = cfg.read_text(encoding="utf-8")
match = re.search(r"(?m)^\s*api_key:\s*(\S+)\s*$", text)
if not match:
    sys.stderr.write("no api_key in Douzy config\n")
    sys.exit(1)
key = match.group(1).strip().strip("'\"")
if not key:
    sys.stderr.write("empty api_key\n")
    sys.exit(1)
sys.stdout.write(key)
