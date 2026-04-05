"""vcli — a tiny agent framework for virtual CLI commands. See README.md."""

import shlex
from dataclasses import dataclass
from typing import Callable

from vcli import tools


def split_quoted(line: str, seps: str) -> list[str]:
    """Split `line` on any char in `seps`, ignoring separators inside quotes."""
    out, cur = [], []
    in_single = in_double = escape = False
    for ch in line:
        if escape:
            cur.append(ch); escape = False; continue
        if ch == "\\" and in_double:
            cur.append(ch); escape = True; continue
        if ch == "'" and not in_double:
            in_single = not in_single; cur.append(ch); continue
        if ch == '"' and not in_single:
            in_double = not in_double; cur.append(ch); continue
        if ch in seps and not in_single and not in_double:
            out.append("".join(cur)); cur = []; continue
        cur.append(ch)
    out.append("".join(cur))
    return out


@dataclass
class Command:
    name: str
    fn: Callable
    help: str = ""
    invocations: int = 0
    last_index: int = 0


class Agent:
    def __init__(self, name: str = "vcli"):
        self.name = name
        self.commands: dict[str, Command] = {}
        self.context: dict = {}
        self._printed_live = False  # set by commands that write directly to stdout
        self._next_index = 0
        tools.register_core(self)

    def cmd(self, name: str | None = None, help: str = ""):
        def decorator(fn):
            cmd_name = name or fn.__name__
            self.commands[cmd_name] = Command(cmd_name, fn, help or fn.__doc__ or "")
            return fn
        return decorator

    def execute(self, line: str) -> str:
        """Parse and run a command line, supporting pipes (|) and chains (;)."""
        line = line.strip()
        if not line:
            return ""
        outputs = []
        for segment in split_quoted(line, ";"):
            segment = segment.strip()
            if not segment:
                continue
            result = self._run_pipeline(segment)
            if result:
                outputs.append(result)
        return "\n".join(outputs)

    def _run_pipeline(self, line: str) -> str:
        piped: str | None = None
        for stage in split_quoted(line, "|"):
            stage = stage.strip()
            if not stage:
                continue
            try:
                parts = shlex.split(stage)
            except ValueError as e:
                return f"parse error: {e}"
            piped = self._run_one(parts, piped)
            if piped.startswith(("unknown command:", "error:", "parse error:")):
                return piped
        return piped or ""

    def _run_one(self, parts: list[str], piped: str | None) -> str:
        name, args = parts[0], parts[1:]
        if name not in self.commands:
            return f"unknown command: {name}\nType 'help' for available commands."
        # Expose raw piped input on the agent so commands that care (e.g. curl
        # with -d @-) can read it without it being mixed into their positional
        # args. Still append splitlines to args for back-compat with simple
        # pipe-friendly commands (grep, head, sed, ...).
        self._piped = piped
        if piped is not None:
            args = args + piped.splitlines()
        cmd = self.commands[name]
        self._next_index += 1
        cmd.invocations += 1
        cmd.last_index = self._next_index
        try:
            result = cmd.fn(args)
            return str(result) if result is not None else ""
        except SystemExit:
            raise
        except Exception as e:
            return f"error: {e}"

    def run(self, prompt: str | None = None, initial: str | None = None):
        """REPL loop with optional auto-chain driver.

        Context hooks (all optional):
          - context['unwrap'](output) -> str        : post-process every output
          - context['extract_command'](text) -> str : if truthy, run it as next input
          - context['max_steps']                    : stop auto-chaining after N commands
        """
        prompt = prompt or f"{self.name}> "
        print(f"{self.name} ready. Type 'help' for commands.")
        pending = initial
        while True:
            if pending is not None:
                line, pending = pending, None
                print(f"{prompt}{line}")
            else:
                try:
                    line = input(prompt)
                except (EOFError, KeyboardInterrupt):
                    print(); break

            self._printed_live = False
            output = self.execute(line)

            unwrap = self.context.get("unwrap")
            if callable(unwrap):
                output = unwrap(output)

            extract = self.context.get("extract_command")
            next_cmd = extract(output) if callable(extract) else None

            if output and not self._printed_live:
                print(output)

            if next_cmd:
                max_steps = self.context.get("max_steps")
                if max_steps is not None and self._next_index >= int(max_steps):
                    print(f"[max_steps={max_steps} reached at command index {self._next_index}]")
                    continue
                pending = next_cmd

