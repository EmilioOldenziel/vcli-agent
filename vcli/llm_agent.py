"""LLM agent loop. The LLM writes its own vcli chains; every chain must end
with a self-curl back to {endpoint} to keep the loop going. See AGENT.md."""

import json
import re
import shlex

from vcli import Agent, split_quoted

agent = Agent(name="llm")
agent.context.update({
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "max_tokens": 4096,
    "messages": [],
    "memory": {},
    # Qwen3: disable verbose reasoning so the model emits visible content directly.
    "extra_payload": {"chat_template_kwargs": {"enable_thinking": False}},
    "max_steps": 24,
})

# Only these tools may appear in a chain the LLM writes.
ALLOWED_TOOLS = {"curl", "pack", "grep", "memory", "ask_human", "echo", "read", "sed", "head"}


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


@agent.cmd(name="echo", help="Echo args (and any piped input) back as a single line")
def _echo(args):
    return " ".join(args)


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


@agent.cmd(name="pack", help="Wrap piped text into a chat-completions JSON body")
def _pack(args):
    text = "\n".join(args).strip("\n") if args else ""
    if not text:
        return "usage: <text> | pack  or  pack <text>"
    text = text.replace("{endpoint}", agent.context.get("endpoint", ""))
    text = text.replace("{model}", agent.context.get("model", ""))

    messages = agent.context["messages"]
    if not messages:
        # First turn: install brief as system, add a minimal user kickoff
        # (Qwen3 chat templates reject system-only requests).
        messages.append({"role": "system", "content": text})
        messages.append({"role": "user", "content": "Begin."})
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


def _extract_cmd_line(text: str) -> str:
    """Return the chain embedded in text (after CMD: / DONE:), or ""."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("CMD:"):
            return line[4:].strip().strip("`")
        if upper.startswith("DONE:") or upper == "DONE":
            return line
    return ""


def _pipeline_tools(cmd_line: str) -> list[str]:
    tools = []
    for segment in split_quoted(cmd_line, ";"):
        for stage in split_quoted(segment, "|"):
            stage = stage.strip()
            if stage:
                tools.append(stage.split(None, 1)[0])
    return tools


def _unwrap_hook(output: str) -> str:
    """If output is a chat-completions JSON body, return assistant content and
    record it in history so the next `pack` sees a coherent conversation."""
    s = output.lstrip()
    if not s.startswith("{"):
        return output
    try:
        msg = json.loads(s)["choices"][0]["message"]
    except (KeyError, ValueError, IndexError, TypeError):
        return output
    content = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    if not content:
        return output
    agent.context["messages"].append({"role": "assistant", "content": content})
    return content


def _extract_hook(output: str) -> str:
    """Return a follow-up command if output is executable: either a CMD: line,
    or a single-line pipeline whose stages are all whitelisted tools."""
    cmd = _extract_cmd_line(output)
    if cmd:
        if cmd.upper().startswith("DONE"):
            return ""
        bad = [t for t in _pipeline_tools(cmd) if t not in ALLOWED_TOOLS]
        if bad:
            print(f"[rejected: tool(s) {bad} not allowed. Allowed: {sorted(ALLOWED_TOOLS)}]")
            return ""
        return cmd

    text = output.strip()
    if not text or "\n" in text:
        return ""
    tools = _pipeline_tools(text)
    if tools and all(t in ALLOWED_TOOLS for t in tools):
        return text
    return ""


agent.context["unwrap"] = _unwrap_hook
agent.context["extract_command"] = _extract_hook


@agent.cmd(name="init", help="Bootstrap: read AGENT.md | pack | curl {endpoint}")
def _init(args):
    """Return the bootstrap chain as a bare string. The auto-chain driver in
    Agent.run recognizes it (all whitelisted tools) and runs it next."""
    brief = agent.context.get("brief", "AGENT.md")
    endpoint = agent.context["endpoint"]
    agent.context["messages"] = []
    return (
        f"read {shlex.quote(brief)} "
        f"| pack "
        f"| curl -X POST {shlex.quote(endpoint)} "
        f"-H 'Content-Type: application/json' -m 300 -d @-"
    )


if __name__ == "__main__":
    agent.run()
