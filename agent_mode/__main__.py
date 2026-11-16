"""`python -m agent_mode <verb> ...` 入口。"""

import sys

from agent_mode.entry import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
