"""Built-in tools (commands) for vcli agents.

`register_core` installs the text/HTTP tools available to every Agent.
`register_llm` installs the extra tools the LLM-driven agent needs
(pack/memory/ask_human/ask_agent and chat-completion context commands).
"""

import datetime
import json
import re
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# core tools — registered automatically by Agent.__init__
# ---------------------------------------------------------------------------


def register_core(agent):
    """Register the default built-in commands on `agent`."""

    @agent.cmd(name="help", help="List available commands")
    def _help(args):
        return "\n".join(
            f"  {c.name:16s} {c.help}"
            for c in sorted(agent.commands.values(), key=lambda c: c.name)
        )

    @agent.cmd(name="exit", help="Exit the agent")
    def _exit(args):
        raise SystemExit(0)

    @agent.cmd(name="echo", help="Echo args (and any piped input) back as a single line")
    def _echo(args):
        return " ".join(args)

    @agent.cmd(name="upper", help="Uppercase piped input")
    def _upper(args):
        return " ".join(args).upper()

    @agent.cmd(name="lower", help="Lowercase piped input")
    def _lower(args):
        return " ".join(args).lower()

    @agent.cmd(name="count", help="Count lines in piped input")
    def _count(args):
        return str(len(args))

    @agent.cmd(name="head", help="First N piped lines: head [-n N] (default 10)")
    def _head(args):
        n, lines = 10, list(args)
        if lines and lines[0] == "-n":
            if len(lines) < 2:
                return "usage: head [-n N]"
            try:
                n, lines = int(lines[1]), lines[2:]
            except ValueError:
                return "head: -n requires an integer"
        elif lines and lines[0].startswith("-n"):
            try:
                n, lines = int(lines[0][2:]), lines[1:]
            except ValueError:
                return "head: -n requires an integer"
        return "\n".join(lines[:n])

    @agent.cmd(name="tail", help="Last N piped lines: tail [-n N] (default 10)")
    def _tail(args):
        n, lines = 10, list(args)
        if lines and lines[0] == "-n":
            if len(lines) < 2:
                return "usage: tail [-n N]"
            try:
                n, lines = int(lines[1]), lines[2:]
            except ValueError:
                return "tail: -n requires an integer"
        elif lines and lines[0].startswith("-n"):
            try:
                n, lines = int(lines[0][2:]), lines[1:]
            except ValueError:
                return "tail: -n requires an integer"
        return "\n".join(lines[-n:] if n > 0 else [])

    @agent.cmd(name="wc", help="Count piped input: wc [-l|-w|-c] (default: lines words chars)")
    def _wc(args):
        mode, lines = None, list(args)
        if lines and lines[0] in ("-l", "-w", "-c"):
            mode, lines = lines[0], lines[1:]
        text = "\n".join(lines)
        n_lines = len(lines)
        n_words = sum(len(l.split()) for l in lines)
        n_chars = len(text)
        if mode == "-l":
            return str(n_lines)
        if mode == "-w":
            return str(n_words)
        if mode == "-c":
            return str(n_chars)
        return f"{n_lines} {n_words} {n_chars}"

    @agent.cmd(name="sort", help="Sort piped lines: sort [-r] [-n] [-u]")
    def _sort(args):
        reverse = numeric = unique = False
        lines: list[str] = []
        for a in args:
            if a == "-r":
                reverse = True
            elif a == "-n":
                numeric = True
            elif a == "-u":
                unique = True
            elif a == "-rn" or a == "-nr":
                reverse = numeric = True
            else:
                lines.append(a)
        if numeric:
            def key(s):
                try:
                    return (0, float(s.strip().split()[0]) if s.strip() else 0.0)
                except ValueError:
                    return (1, s)
            out = sorted(lines, key=key, reverse=reverse)
        else:
            out = sorted(lines, reverse=reverse)
        if unique:
            seen, dedup = set(), []
            for l in out:
                if l not in seen:
                    seen.add(l); dedup.append(l)
            out = dedup
        return "\n".join(out)

    @agent.cmd(name="uniq", help="Drop adjacent duplicate lines: uniq [-c]")
    def _uniq(args):
        count = False
        lines: list[str] = []
        for a in args:
            if a == "-c":
                count = True
            else:
                lines.append(a)
        out, prev, run = [], object(), 0
        for l in lines:
            if l == prev:
                run += 1
            else:
                if run:
                    out.append(f"{run:7d} {prev}" if count else prev)
                prev, run = l, 1
        if run:
            out.append(f"{run:7d} {prev}" if count else prev)
        return "\n".join(out)

    @agent.cmd(name="tee", help="Stash piped input in memory KEY and pass it through: tee KEY")
    def _tee(args):
        if not args:
            return "usage: tee KEY  (stashes piped input into memory[KEY])"
        key, lines = args[0], args[1:]
        text = "\n".join(lines)
        store = agent.context.setdefault("memory", {})
        store[key] = text
        return text

    @agent.cmd(name="url", help="URL-encode/decode: url encode|decode [TEXT]  (also reads piped input)")
    def _url(args):
        if not args:
            return "usage: url encode|decode [TEXT]"
        sub, rest = args[0], args[1:]
        text = " ".join(rest) if rest else ""
        if sub == "encode":
            return urllib.parse.quote(text, safe="")
        if sub == "decode":
            return urllib.parse.unquote(text)
        return f"url: unknown subcommand '{sub}' (use encode|decode)"

    @agent.cmd(name="date", help="Current date/time: date [-u] [+FORMAT]  (default ISO 8601)")
    def _date(args):
        utc = False
        fmt = None
        for a in args:
            if a == "-u":
                utc = True
            elif a.startswith("+"):
                fmt = a[1:]
        now = datetime.datetime.now(datetime.timezone.utc) if utc else datetime.datetime.now().astimezone()
        return now.strftime(fmt) if fmt else now.isoformat(timespec="seconds")

    @agent.cmd(name="grep", help="Filter lines matching a pattern: grep PATTERN")
    def _grep(args):
        if not args:
            return "usage: grep PATTERN"
        pattern, lines = args[0], args[1:]
        return "\n".join(l for l in lines if pattern in l)

    @agent.cmd(name="sed", help="Stream-edit piped lines: sed s/PATTERN/REPL/[g]")
    def _sed(args):
        if not args:
            return "usage: sed s/PATTERN/REPL/[g]"
        expr, lines = args[0], args[1:]
        if len(expr) < 2 or expr[0] != "s":
            return "sed: only s/PATTERN/REPL/[flags] is supported"
        delim = expr[1]
        parts = expr[2:].split(delim)
        if len(parts) < 2:
            return "sed: malformed expression"
        pattern, repl = parts[0], parts[1]
        flags = parts[2] if len(parts) > 2 else ""
        count = 0 if "g" in flags else 1
        try:
            rx = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
        except re.error as e:
            return f"sed: bad pattern: {e}"
        return "\n".join(rx.sub(repl, line, count=count) for line in lines)

    @agent.cmd(name="cut", help="Select fields/chars from piped lines: cut -d DELIM -f LIST | cut -c LIST")
    def _cut(args):
        delim = "\t"
        fields_spec = None
        chars_spec = None
        output_delim = None
        lines: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-d" and i + 1 < len(args):
                delim = args[i + 1]; i += 2
            elif a == "-f" and i + 1 < len(args):
                fields_spec = args[i + 1]; i += 2
            elif a == "-c" and i + 1 < len(args):
                chars_spec = args[i + 1]; i += 2
            elif a == "--output-delimiter" and i + 1 < len(args):
                output_delim = args[i + 1]; i += 2
            else:
                lines.append(a); i += 1

        if fields_spec is None and chars_spec is None:
            return "usage: cut -d DELIM -f LIST  |  cut -c LIST"
        if output_delim is None:
            output_delim = delim if fields_spec is not None else ""

        out = []
        for line in lines:
            if fields_spec is not None:
                parts = line.split(delim)
                idxs = _parse_ranges(fields_spec, len(parts))
                if isinstance(idxs, str):
                    return idxs
                picked = [parts[n - 1] for n in idxs if 1 <= n <= len(parts)]
                out.append(output_delim.join(picked))
            else:
                idxs = _parse_ranges(chars_spec, len(line))
                if isinstance(idxs, str):
                    return idxs
                out.append("".join(line[n - 1] for n in idxs if 1 <= n <= len(line)))
        return "\n".join(out)

    @agent.cmd(name="awk", help="Minimal awk: awk [-F SEP] '{print $1, $3}' — print statement only")
    def _awk(args):
        sep = None  # None => whitespace split
        program = None
        lines: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-F" and i + 1 < len(args):
                sep = args[i + 1]; i += 2
            elif a.startswith("-F") and len(a) > 2:
                sep = a[2:]; i += 1
            elif program is None:
                program = a; i += 1
            else:
                lines.append(a); i += 1

        if program is None:
            return "usage: awk [-F SEP] '{print $1, $3}'"
        program = program.strip()
        m = _AWK_PRINT_RX.match(program)
        if not m:
            return "awk: only '{print ...}' programs are supported"
        expr = m.group(1).strip()

        pieces = _split_awk_args(expr) if expr else []
        if isinstance(pieces, str):
            return pieces

        out = []
        for line in lines:
            fields = line.split(sep) if sep is not None else line.split()
            rendered = []
            for p in pieces:
                if not p:
                    rendered.append(""); continue
                if p.startswith('"') and p.endswith('"') and len(p) >= 2:
                    rendered.append(p[1:-1].encode().decode("unicode_escape"))
                elif p == "$0":
                    rendered.append(line)
                elif p.startswith("$"):
                    try:
                        n = int(p[1:])
                    except ValueError:
                        return f"awk: bad field reference '{p}'"
                    rendered.append(fields[n - 1] if 1 <= n <= len(fields) else "")
                else:
                    return f"awk: unsupported token '{p}'"
            out.append(" ".join(rendered))
        return "\n".join(out)

    @agent.cmd(name="cat", help="Read a file's contents: cat PATH")
    def _cat(args):
        if not args:
            return "usage: cat PATH"
        try:
            with open(args[0], "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"error: {e}"

    @agent.cmd(name="curl", help="HTTP fetch: curl [-X M] [-H H] [-d D | -d @-] [-m T] [-N] URL")
    def _curl(args):
        return _curl_impl(agent, args)


# ---------------------------------------------------------------------------
# LLM-agent tools — opt in with register_llm(agent)
# ---------------------------------------------------------------------------


def register_llm(agent):
    """Register the LLM-driven agent's extra commands on `agent`."""

    @agent.cmd(name="endpoint", help="Set or show the chat-completions URL")
    def _endpoint(args):
        if not args:
            return agent.context["endpoint"]
        agent.context["endpoint"] = args[0]
        return f"endpoint = {args[0]}"

    @agent.cmd(name="model", help="Set or show the model name")
    def _model(args):
        if not args:
            return agent.context["model"]
        agent.context["model"] = args[0]
        return f"model = {args[0]}"

    @agent.cmd(name="history", help="Show the conversation so far")
    def _history(args):
        msgs = agent.context["messages"]
        return "\n".join(f"{m['role']}: {m['content']}" for m in msgs) if msgs else "(empty)"

    @agent.cmd(name="reset", help="Clear the conversation history")
    def _reset(args):
        agent.context["messages"] = []
        return "conversation cleared"

    @agent.cmd(name="pack", help="Wrap piped text into a chat-completions JSON body")
    def _pack(args):
        text = "\n".join(args).strip("\n") if args else ""
        if not text:
            return "usage: <text> | pack  or  pack <text>"
        text = text.replace("{endpoint}", agent.context.get("endpoint", ""))
        text = text.replace("{model}", agent.context.get("model", ""))
        text = text.replace("{auth_header}", agent.context.get("auth_header", ""))

        messages = agent.context["messages"]
        if not messages:
            # First turn: install brief as system, add a minimal user kickoff
            # (Qwen3 chat templates reject system-only requests).
            messages.append({"role": "system", "content": text})
            seed = agent.context.get("seed") or "Begin."
            messages.append({"role": "user", "content": seed})
        else:
            messages.append({"role": "user", "content": text})

        body = {
            "model": agent.context["model"],
            "messages": messages,
            "max_tokens": agent.context.get("max_tokens", 512),
        }
        body.update(agent.context.get("extra_payload", {}))
        return json.dumps(body)

    @agent.cmd(name="memory", help="Scratchpad: memory get|set|list|del [KEY] [VALUE...]")
    def _memory(args):
        store = agent.context.setdefault("memory", {})
        if not args:
            return "usage: memory get|set|list|del [KEY] [VALUE...]"
        sub, rest = args[0], args[1:]

        if sub == "list":
            if not store:
                return "(empty)"
            return "\n".join(
                f"{k}: {(v.replace(chr(10), ' ')[:57] + '...') if len(v) > 60 else v.replace(chr(10), ' ')}"
                for k, v in store.items()
            )
        if sub == "get":
            return store.get(rest[0], "(unset)") if rest else "usage: memory get KEY"
        if sub == "set":
            if len(rest) < 2:
                return "usage: memory set KEY VALUE  (or pipe input in)"
            key, value = rest[0], " ".join(rest[1:])
            store[key] = value
            return f"{key} stored ({len(value)} chars)"
        if sub == "del":
            if not rest:
                return "usage: memory del KEY"
            if rest[0] in store:
                del store[rest[0]]
                return f"{rest[0]} deleted"
            return f"{rest[0]} not set"
        return f"memory: unknown subcommand '{sub}' (use get|set|list|del)"

    @agent.cmd(name="ask_human", help="Yield control to the human: ask_human <question>")
    def _ask_human(args):
        question = " ".join(args).strip()
        if question:
            print(f"\n[agent asks] {question}")
        try:
            return input("human> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "DONE"

    @agent.cmd(
        name="ask_agent",
        help="Send a prompt to the LLM (bootstraps AGENT.md on first call, "
             "appends a new user turn thereafter)",
    )
    def _ask_agent(args):
        """Return a chain that packs a user message and self-curls the endpoint.
        The auto-chain driver in Agent.run recognizes the chain (all whitelisted
        tools) and runs it next.

        - First call: loads AGENT.md as the system prompt and seeds the user
          turn with the provided question (or "Begin." if none was given).
        - Subsequent calls: appends the question as a new user turn against the
          existing conversation history — no system reload.
        """
        endpoint = agent.context["endpoint"]
        auth = agent.context.get("auth_header", "")
        question = " ".join(args).strip()
        curl_tail = (
            f"| curl -X POST {shlex.quote(endpoint)} "
            f"-H 'Content-Type: application/json' {auth} -m 300 -d @-"
        )

        if not agent.context.get("messages"):
            brief = agent.context.get("brief", "AGENT.md")
            if question:
                agent.context["seed"] = question
            return f"cat {shlex.quote(brief)} | pack {curl_tail}"

        if not question:
            return "ask_agent: need a question after the first turn"
        return f"echo {shlex.quote(question)} | pack {curl_tail}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_AWK_PRINT_RX = re.compile(r"^\{\s*print\s*(.*?)\s*\}$")


def _parse_ranges(spec: str, length_hint: int | None = None) -> list[int] | str:
    """Parse a cut-style field/char spec like '1,3-5,7' into 1-based indices.
    Returns an error string on failure."""
    indices: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            try:
                lo = int(lo_s) if lo_s else 1
                hi = int(hi_s) if hi_s else (length_hint or lo)
            except ValueError:
                return f"cut: bad range '{part}'"
            if lo < 1 or hi < lo:
                return f"cut: bad range '{part}'"
            indices.extend(range(lo, hi + 1))
        else:
            try:
                indices.append(int(part))
            except ValueError:
                return f"cut: bad field '{part}'"
    return indices


def _split_awk_args(s: str) -> list[str] | str:
    """Split an awk print arg list on top-level commas. Items are $N field
    references, "$0", or double-quoted literals."""
    items, cur, in_str, escape = [], [], False, False
    for ch in s:
        if escape:
            cur.append(ch); escape = False; continue
        if ch == "\\" and in_str:
            cur.append(ch); escape = True; continue
        if ch == '"':
            in_str = not in_str; cur.append(ch); continue
        if ch == "," and not in_str:
            items.append("".join(cur).strip()); cur = []; continue
        cur.append(ch)
    if in_str:
        return "awk: unterminated string"
    tail = "".join(cur).strip()
    if tail or items:
        items.append(tail)
    return items


def _curl_impl(agent, args: list[str]) -> str:
    method, headers, data, url = "GET", {}, None, None
    timeout, stream, body_from_stdin = 30.0, False, False
    extras: list[str] = []

    # Separate piped stdin (from previous pipeline stage) from command-line args.
    # _run_one appends piped.splitlines() to args; strip that tail so that only
    # the true CLI tokens drive flag parsing, and the piped bytes are used
    # verbatim as the request body when -d @- is set. Mixing them caused
    # invalid JSON bodies when extras contained stray tokens alongside the
    # packed JSON (extras was joined with "\n", producing "garbage\n{...}").
    piped = getattr(agent, "_piped", None)
    if piped is not None:
        n_piped = len(piped.splitlines())
        args = args[: len(args) - n_piped] if n_piped else list(args)

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
                extras.append(a)  # stray positional args (ignored)
            i += 1

    if body_from_stdin:
        if piped is None:
            return "error: curl -d @- used with no piped input"
        data = piped.encode("utf-8")
    if not url:
        return "usage: curl [-X M] [-H H] [-d D | -d @-] [-m T] [-N] URL"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not stream:
                return r.read().decode("utf-8", errors="replace")
            # Streaming: print to stdout live, also return full body for callers.
            agent._printed_live = True
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
