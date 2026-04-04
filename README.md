# vcli

A tiny Python framework for building virtual CLI agents — register commands with a decorator, run an interactive REPL, compose commands with pipes and chains.

## The idea

`vcli` is a minimalist experiment in **pipe-native agent loops**. Instead of the usual tool-calling harness that hides the LLM behind JSON tool schemas and a driver loop, vcli treats the LLM as just another command in a Unix-style pipeline. The LLM's reply **is** the next command line; its output pipes into the next curl call; the whole "agent" is a chain of pipes you can read left to right:

```
ask_human  →  curl(llm)  →  grep  →  curl(llm)  →  ask_human  →  curl(llm)  →  DONE
```

Every "thinking" step is a real curl POST to a chat-completions endpoint. Every "doing" step is a real command (`curl`, `grep`, `memory`, `ask_human`). There is no hidden driver — the loop is literally strings being piped between commands.

See `AGENT.md` for the full protocol; it doubles as the system prompt the LLM reads when `init` runs.

## Basic usage

```python
from vcli import Agent

agent = Agent(name="demo")

@agent.cmd(name="echo", help="Echo back arguments")
def echo(args):
    return " ".join(args)

if __name__ == "__main__":
    agent.run()
```

Run the included example:

```bash
python -m vcli.example
```

## Features

- **Decorator-based commands** — `@agent.cmd(name=..., help=...)`
- **Shared context** — `agent.context` dict for cross-command state
- **Pipes** — `echo hello | upper` threads output into the next command's args
- **Chains** — `echo a; echo b` runs commands sequentially
- **Built-ins** — `help`, `exit`, `upper`, `lower`, `count`, `head [N]`, `grep PATTERN`, `read PATH`, `curl ... URL`

## Writing commands

A command is a function that takes a list of string args and returns a string. When piped, the upstream output is split into lines and appended to `args`:

```python
@agent.cmd(name="reverse", help="Reverse each line")
def reverse(args):
    return "\n".join(line[::-1] for line in args)
```

Return `None` or an empty string to suppress output. Raise `SystemExit` to exit the REPL.

## Example session

```
demo> echo hello world | upper
HELLO WORLD
demo> help | grep upper
  upper            Uppercase piped input
demo> echo one; echo two; echo three
one
two
three
```

## The auto-chain driver

`Agent.run` supports an optional auto-chain mode: if `context['extract_command']` is set and returns a non-empty string from a command's output, the REPL treats that string as the next command to execute — no stdin read in between. Combined with `context['unwrap']` (which preprocesses every output) and `context['max_steps']` (step budget), this is enough to turn any command whose output is itself a command into a driver for the next step. This is how `llm_agent.py` implements its LLM loop without a dedicated driver.

## The LLM agent loop

Point vcli at any OpenAI-compatible chat endpoint (OpenAI, llama.cpp server, Ollama's compat API, etc.) and run `init`:

```bash
python -m vcli.llm_agent
llm> endpoint http://0.0.0.0:8080/v1/chat/completions
llm> model unsloth/Qwen3.5-9B-GGUF:Q4_K_M
llm> init
```

Or use the one-shot bootstrap:

```bash
VCLI_ENDPOINT=http://0.0.0.0:8080/v1/chat/completions \
VCLI_MODEL=unsloth/Qwen3.5-9B-GGUF:Q4_K_M \
python -m vcli.bootstrap_example
```

`init` emits a bootstrap chain — `read AGENT.md | pack | curl -d @-` — that installs `AGENT.md` as the system prompt and POSTs the first request. The LLM's reply is itself the next chain, which must end in another self-curl to keep the loop alive. The loop terminates when:

- the model replies `DONE: ...`, or
- the chain does not end in a self-curl, or
- an HTTP/parse error occurs, or
- `context['max_steps']` is reached.

The LLM's allowed toolset during the loop is restricted to: `curl`, `pack`, `grep`, `memory`, `ask_human`, `echo`, `read`, `sed`, `head`. Any other command in a pipeline stage causes the whole chain to be rejected and the error fed back to the model as its next input.
