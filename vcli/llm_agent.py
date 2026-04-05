"""LLM agent loop. The LLM writes its own vcli chains; every chain must end
with a self-curl back to {endpoint} to keep the loop going. See AGENT.md."""

import json

from vcli import Agent, split_quoted
from vcli import tools

agent = Agent(name="llm")
agent.context.update({
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "max_tokens": 4096,
    "messages": [],
    "memory": {},
    # Pre-formatted curl header fragment (e.g. "-H 'Authorization: Bearer sk-...'").
    # Empty by default; set this when talking to hosted providers like OpenAI.
    "auth_header": "",
    # Provider-specific extra fields merged into the request body. Empty for
    # OpenAI; set e.g. {"chat_template_kwargs": {"enable_thinking": False}} for Qwen3.
    "extra_payload": {},
    "max_steps": 30,
})

tools.register_llm(agent)

# Only these tools may appear in a chain the LLM writes.
ALLOWED_TOOLS = {
    "curl", "pack", "grep", "memory", "ask_human", "echo", "read",
    "sed", "head", "tail", "cut", "awk", "wc", "sort", "uniq", "tee",
    "url", "date", "help",
}


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
    pipeline = _pipeline_tools(text)
    if pipeline and all(t in ALLOWED_TOOLS for t in pipeline):
        return text
    return ""


agent.context["unwrap"] = _unwrap_hook
agent.context["extract_command"] = _extract_hook


if __name__ == "__main__":
    import os
    import sys

    endpoint = os.environ.get("VCLI_ENDPOINT")
    if endpoint:
        agent.context["endpoint"] = endpoint

    model = os.environ.get("VCLI_MODEL")
    if model:
        agent.context["model"] = model

    key = os.environ.get("VCLI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        q = chr(39)
        agent.context["auth_header"] = f"-H {q}Authorization: Bearer {key}{q}"

    initial = None
    if not sys.stdin.isatty():
        initial = sys.stdin.read().strip() or None
        # Reconnect stdin to the tty so the REPL / ask_human can still read input
        # after the initial command runs.
        try:
            sys.stdin = open("/dev/tty")
        except OSError:
            pass

    agent.run(initial=initial)
