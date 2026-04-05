# vcli

A tiny Python framework for building virtual CLI agents — register commands with a decorator, run an interactive REPL, compose commands with pipes and chains.

**Try it in one line** (needs an OpenAI key, nothing else — no install, no deps):

```bash
git clone https://github.com/EmilioOldenziel/vcli-agent && cd vcli-agent && OPENAI_API_KEY=sk-... python3 -c 'import os; from vcli.llm_agent import agent; k=os.environ["OPENAI_API_KEY"]; q=chr(39); agent.context["auth_header"]=f"-H {q}Authorization: Bearer {k}{q}"; agent.run(initial="ask_agent ask me for a github repo (owner/name), then fetch its latest release tag and report it back to me")'
```

You'll watch `gpt-4o-mini` write its own unix pipelines, pause mid-loop to call `ask_human` for the repo name, self-curl back to itself each turn, and finish with `DONE:`.

## Why this exists

I built vcli to understand, for myself, what an **LLM harness** actually is — stripped of frameworks, SDKs, and orchestration jargon. A harness is just a loop: the model emits text, something parses that text into a tool call, the tool runs, its output is fed back, the model gets another turn. Everything else is decoration. To make that shape impossible to hide from, I wrote it in **pure Python** with tools as ordinary functions in a dict. No eval, no subprocess, no shelling out — the sandbox *is* the Python runtime, and the set of things the model can do is exactly the set of functions you chose to register. That constraint is the whole point: a harness is not a security boundary bolted on after the fact, it is the verbs you exposed.

## The idea

`vcli` is a minimalist experiment in **pipe-native agent loops**. Instead of the usual tool-calling harness that hides the LLM behind JSON tool schemas and a driver loop, vcli treats the LLM as just another command in a Unix-style pipeline. The LLM's reply **is** the next command line; its output pipes into the next curl call; the whole "agent" is a chain of pipes you can read left to right:

```
ask_human  →  curl(llm)  →  grep  →  curl(llm)  →  ask_human  →  curl(llm)  →  DONE
```

Every "thinking" step is a real curl POST to a chat-completions endpoint. Every "doing" step is a real command (`curl`, `grep`, `memory`, `ask_human`). There is no hidden driver — the loop is literally strings being piped between commands.

Crucially, the "commands" are **virtual**: `grep`, `sed`, `awk`, `cut`, `curl` and friends are not shell-outs to the host's binaries, they are small pure-Python functions over strings and `urllib`. Nothing the LLM writes ever becomes a subprocess, a shell invocation, or an `eval`. That means the harness does not need Docker, seccomp, namespaces, or chroot to stay bounded — it never touches the host in the first place. The only verbs that exist are the ones registered on the agent, and the sandbox is exactly that registry. The planned virtual filesystem (see *Next steps*) closes the last seam by moving `read` and `tee` off the real disk too.

See `AGENT.md` for the full protocol; it doubles as the system prompt the LLM reads when `ask_agent` runs.

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
- **Built-in unix-style toolkit** (see `vcli/tools.py`):
  - text: `echo`, `upper`, `lower`, `grep`, `sed`, `cut`, `awk`, `head`, `tail`, `sort`, `uniq`, `wc`, `count`
  - I/O: `read`, `curl`, `tee`, `url`, `date`
  - meta: `help`, `exit`

## Writing commands

A command is a function that takes a list of string args and returns a string. When piped, the upstream output is split into lines and appended to `args`:

```python
@agent.cmd(name="reverse", help="Reverse each line")
def reverse(args):
    return "\n".join(line[::-1] for line in args)
```

Return `None` or an empty string to suppress output. Raise `SystemExit` to exit the REPL.

## Example session

The built-ins are deliberately shaped like the unix tools you already know, so a pipeline reads the same way in vcli as it would in bash. This matters because the *same grammar* is what the LLM writes during the agent loop — there is nothing special about how it composes work.

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

**Text munging with the usual suspects:**

```
demo> read /etc/hosts | grep localhost | wc -l
2

demo> read data.tsv | cut -f 1,3 | sort -u | head -n 5
alice   admin
bob     editor
carol   viewer
...

demo> echo "hello world" | sed s/world/vcli/ | upper
HELLO VCLI

demo> echo "2025-01-15,login,alice" | awk -F , '{print $3, $2}'
alice login
```

**Counting and ranking (sort | uniq -c is the classic one-liner):**

```
demo> read access.log | awk '{print $1}' | sort | uniq -c | sort -rn | head -n 3
     42 10.0.0.1
     17 10.0.0.7
      9 192.168.1.5
```

**HTTP + JSON + URL encoding (the `curl` built-in is real `urllib`):**

```
demo> echo "rainy day in amsterdam" | url encode
rainy%20day%20in%20amsterdam

demo> curl https://api.github.com/repos/ggerganov/llama.cpp/releases/latest | grep tag_name
  "tag_name": "b8562",

demo> date +%Y-%m-%d
2026-04-05
```

**`tee` bridges pipelines and the `memory` scratchpad** — save a value mid-pipe and keep flowing:

```
llm> curl https://api.github.com/repos/ggerganov/llama.cpp/releases/latest | grep tag_name | tee latest_tag | upper
  "TAG_NAME": "B8562",
llm> memory get latest_tag
  "tag_name": "b8562",
```

**Chains (`;`) run commands in sequence; pipes (`|`) thread output forward.** Combined, they let a single line express a small program:

```
demo> date; read TODO.md | head -n 3; echo --- ; read TODO.md | wc -l
2026-04-05T14:56:05+02:00
# TODO
- ship v0.2
- write docs
---
27
```

The point of vcli is that **this same grammar is what the LLM writes**. When `llm_agent.py` runs the agent loop, the model's reply is literally a line like the ones above, ending in a `curl` back to itself. No tool-call JSON, no hidden driver — the LLM is composing unix pipelines, and you can read what it did left to right.

## The auto-chain driver

`Agent.run` supports an optional auto-chain mode: if `context['extract_command']` is set and returns a non-empty string from a command's output, the REPL treats that string as the next command to execute — no stdin read in between. Combined with `context['unwrap']` (which preprocesses every output) and `context['max_steps']` (step budget), this is enough to turn any command whose output is itself a command into a driver for the next step. This is how `llm_agent.py` implements its LLM loop without a dedicated driver.

## The LLM agent loop

vcli talks to any OpenAI-compatible chat endpoint. No dependencies, no SDK — just `urllib`.

### Quickstart: OpenAI (zero setup)

`vcli.llm_agent` is a plain library module — no `__main__`. You drive it with a short `python3 -c '...'` snippet that imports the pre-built `agent`, sets the auth header from your key, and calls `agent.run(initial="ask_agent <question>")`:

```bash
OPENAI_API_KEY=sk-... python3 -c 'import os; from vcli.llm_agent import agent; k=os.environ["OPENAI_API_KEY"]; q=chr(39); agent.context["auth_header"]=f"-H {q}Authorization: Bearer {k}{q}"; agent.run(initial="ask_agent ask me for a github repo (owner/name), then fetch its latest release tag and report it back to me")'
```

The agent context already defaults to `https://api.openai.com/v1/chat/completions` with `gpt-4o-mini`, so the snippet only has to inject the bearer header. `ask_agent <question>` is the chain you hand it: on the first call it loads `AGENT.md` as the system prompt and seeds the conversation with your question, then the LLM takes over, writing its own pipelines and self-curling each turn until it replies `DONE:`.

Override the model in the same snippet by setting `agent.context["model"]`:

```bash
OPENAI_API_KEY=sk-... python3 -c 'import os; from vcli.llm_agent import agent; k=os.environ["OPENAI_API_KEY"]; q=chr(39); agent.context["model"]="gpt-4o"; agent.context["auth_header"]=f"-H {q}Authorization: Bearer {k}{q}"; agent.run(initial="ask_agent summarize the llama.cpp README")'
```

### Interactive mode

Drop the `initial=` argument to get a REPL instead of auto-kicking the loop, then type `ask_agent <question>` (or any vcli pipeline) at the prompt:

```bash
OPENAI_API_KEY=sk-... python3 -c 'import os; from vcli.llm_agent import agent; k=os.environ["OPENAI_API_KEY"]; q=chr(39); agent.context["auth_header"]=f"-H {q}Authorization: Bearer {k}{q}"; agent.run()'
llm> ask_agent ask me for a github repo (owner/name), then fetch its latest release tag and report it back to me
```

### Other providers (local llama.cpp, Ollama, vLLM, Together, Groq, …)

Any OpenAI-compatible endpoint works — just point `agent.context["endpoint"]` at it and set the model name. Local servers that don't need auth can skip the header entirely:

```bash
# llama.cpp server, no auth
python3 -c 'from vcli.llm_agent import agent; agent.context["endpoint"]="http://0.0.0.0:8080/v1/chat/completions"; agent.context["model"]="unsloth/Qwen3.5-9B-GGUF:Q4_K_M"; agent.run(initial="ask_agent fetch a zen quote")'

# hosted provider with a bearer token
GROQ_API_KEY=gsk_... python3 -c 'import os; from vcli.llm_agent import agent; k=os.environ["GROQ_API_KEY"]; q=chr(39); agent.context["endpoint"]="https://api.groq.com/openai/v1/chat/completions"; agent.context["model"]="llama-3.3-70b-versatile"; agent.context["auth_header"]=f"-H {q}Authorization: Bearer {k}{q}"; agent.run(initial="ask_agent fetch a zen quote")'
```

`ask_agent` emits a chain that packs a user message and POSTs it to the endpoint. On the first call it prepends `read AGENT.md |` so the brief is installed as the system prompt; on later calls it just appends a new user turn against the running history. Either way, the LLM's reply is itself the next chain, which must end in another self-curl to keep the loop alive. The loop terminates when:

- the model replies `DONE: ...`, or
- the chain does not end in a self-curl, or
- an HTTP/parse error occurs, or
- `context['max_steps']` is reached.

The LLM's allowed toolset during the loop is restricted to: `curl`, `pack`, `grep`, `memory`, `ask_human`, `echo`, `read`, `sed`, `head`, `tail`, `cut`, `awk`, `wc`, `sort`, `uniq`, `tee`, `url`, `date`, `help`. Any other command in a pipeline stage causes the whole chain to be rejected and the error fed back to the model as its next input.

## Next steps: a virtual filesystem

The natural extension of the "sandbox = the functions you registered" idea is a **virtual filesystem**, implemented in the same fully-Pythonic way. Today `read` and `tee` touch the real disk; the next step is to introduce an in-process filesystem — a plain Python dict (or a small tree of dicts) mapping paths to contents — and rebuild the familiar filesystem tools on top of it.

The goal is to give the LLM the mental model of a Unix filesystem without ever handing it a real path. Every navigation and lookup stays inside the Python runtime, which keeps the sandbox property intact: the model can only reach what the vfs module put there.

Planned commands, each a small pure-Python function over the vfs dict:

- **`pwd`** — print the current working directory (tracked in `agent.context['cwd']`)
- **`cd <path>`** — change the current directory, with `..`, `~`, and absolute/relative resolution
- **`ls [path]`** — list entries in a directory, with `-l` for metadata
- **`tree [path]`** — recursive pretty-print of the subtree
- **`find <path> -name <pattern>`** — walk the tree and match by name
- **`glob <pattern>`** — shell-style globbing (`**/*.md`, `src/*.py`) against the vfs
- **`mkdir`, `touch`, `rm`, `mv`, `cp`** — mutate the vfs in place
- **`stat <path>`** — size, type, mtime from the node's metadata dict

`read` and `tee` would then be redirected to read from and write to the vfs instead of the host disk, making the whole agent loop runnable with zero filesystem side effects. A session could start by seeding the vfs from a real directory (`vfs.load("./sample_project")`) or from an in-memory fixture, and the LLM would explore it with the same pipelines it already writes:

```
llm> tree / | head -n 20
llm> find /docs -name "*.md" | head -n 5 | read | grep TODO
llm> glob "**/*.py" | wc -l
```

Because each of these is just a function over a dict, the implementation stays in the same register as the rest of vcli: a few dozen lines per tool, no external dependencies, and the harness boundary remains obvious — if a path isn't in the dict, the model cannot reach it.

Beyond ergonomics, the vfs **tightens the perimeter**. Today `read` and `tee` are the last tools in the harness that touch real host state, which means the capability-confinement property has a seam: a model that knows a real path can read it, and a model that controls `tee`'s target can write to it. Moving both onto the vfs closes that seam — the only filesystem the model can observe or mutate is the dict the vfs module populated. Combined with the existing no-shell, no-eval, no-subprocess invariants, this is what lets the harness be "just a Python process" on any OS without needing Docker, seccomp, namespaces, or chroot: there is simply no code path from model output to the host filesystem.
