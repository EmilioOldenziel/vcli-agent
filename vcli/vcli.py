"""vcli — a tiny agent framework for virtual CLI commands. See README.md."""

import shlex
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Callable


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
        self._register_builtins()

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

    def _register_builtins(self):
        @self.cmd(name="help", help="List available commands")
        def _help(args):
            return "\n".join(
                f"  {c.name:16s} {c.help}"
                for c in sorted(self.commands.values(), key=lambda c: c.name)
            )

        @self.cmd(name="exit", help="Exit the agent")
        def _exit(args):
            raise SystemExit(0)

        @self.cmd(name="upper", help="Uppercase piped input")
        def _upper(args):
            return " ".join(args).upper()

        @self.cmd(name="lower", help="Lowercase piped input")
        def _lower(args):
            return " ".join(args).lower()

        @self.cmd(name="count", help="Count lines in piped input")
        def _count(args):
            return str(len(args))

        @self.cmd(name="head", help="First N lines of input (default 1)")
        def _head(args):
            n, lines = 1, list(args)
            if lines:
                first = lines[0]
                if first.isdigit():
                    n, lines = int(first), lines[1:]
                elif first.startswith("-") and first[1:].isdigit():
                    n, lines = int(first[1:]), lines[1:]
                elif first in ("-n", "--lines") and len(lines) >= 2 and lines[1].isdigit():
                    n, lines = int(lines[1]), lines[2:]
            return "\n".join(lines[:n])

        @self.cmd(name="grep", help="Filter lines matching a pattern: grep PATTERN")
        def _grep(args):
            if not args:
                return "usage: grep PATTERN"
            pattern, lines = args[0], args[1:]
            return "\n".join(l for l in lines if pattern in l)

        @self.cmd(name="read", help="Read a file's contents: read PATH")
        def _read(args):
            if not args:
                return "usage: read PATH"
            try:
                with open(args[0], "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"error: {e}"

        @self.cmd(name="curl", help="HTTP fetch: curl [-X M] [-H H] [-d D | -d @-] [-m T] [-N] URL")
        def _curl(args):
            return self._curl(args)

    def _curl(self, args: list[str]) -> str:
        method, headers, data, url = "GET", {}, None, None
        timeout, stream, body_from_stdin = 30.0, False, False
        extras: list[str] = []

        i = 0
        while i < len(args):
            a = args[i]
            if a == "-X" and i + 1 < len(args):
                method = args[i + 1]; i += 2
            elif a == "-H" and i + 1 < len(args):
                h = args[i + 1]
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip()] = v.strip()
                i += 2
            elif a == "-d" and i + 1 < len(args):
                val = args[i + 1]
                if val == "@-":
                    body_from_stdin = True
                else:
                    data = val.encode()
                if method == "GET":
                    method = "POST"
                i += 2
            elif a == "-m" and i + 1 < len(args):
                try:
                    timeout = float(args[i + 1])
                except ValueError:
                    return f"error: -m expects a number, got {args[i + 1]}"
                i += 2
            elif a in ("-N", "--no-buffer", "--stream"):
                stream = True; i += 1
            elif a.startswith("-"):
                i += 1  # unknown flag
            else:
                if url is None:
                    url = a
                else:
                    extras.append(a)  # piped stdin lines destined for body
                i += 1

        if body_from_stdin:
            data = "\n".join(extras).encode()
        if not url:
            return "usage: curl [-X M] [-H H] [-d D | -d @-] [-m T] [-N] URL"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if not stream:
                    return r.read().decode("utf-8", errors="replace")
                # Streaming: print to stdout live, also return full body for callers.
                self._printed_live = True
                chunks = []
                for raw in r:
                    text = raw.decode("utf-8", errors="replace")
                    sys.stdout.write(text); sys.stdout.flush()
                    chunks.append(text)
                if chunks and not chunks[-1].endswith("\n"):
                    sys.stdout.write("\n"); sys.stdout.flush()
                return "".join(chunks)
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"
        except Exception as e:
            return f"error: {e}"
