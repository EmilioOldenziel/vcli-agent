"""Entry point for `python -m vcli`: run the demo agent, optionally piping
an initial command line in on stdin (e.g. `echo "echo hi | upper" | python -m vcli`)."""

import sys

from vcli.cli_example import agent

initial = None
if not sys.stdin.isatty():
    initial = sys.stdin.read().strip() or None
    try:
        sys.stdin = open("/dev/tty")
    except OSError:
        pass

agent.run(initial=initial)
