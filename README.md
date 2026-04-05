# vcli-agent

A tiny Python harness where an LLM agent *is* a virtual unix pipeline: the model's reply is the next command line, every tool (`grep`, `curl`, `sed`, …) is a pure-Python function, and each turn ends in a self-curl that keeps the loop going.

**Try it in one line** (needs inserton of an OpenAI key, nothing else — no install, no deps):

```bash
(
  export OPENAI_API_KEY=[INSERT KEY HERE e.g. sk-...] &&
  git clone https://github.com/EmilioOldenziel/vcli-agent &&
  cd vcli-agent &&
  echo "ask_agent ask me for a github repo (owner/name), \
  then fetch its latest release tag and report it back to me" |
  python3 -m vcli.llm_agent
)
```

You'll watch `gpt-4o-mini` write its own unix pipelines, pause mid-loop to call `ask_human` for the repo name, self-curl back to itself each turn, and finish with `DONE:`.

Example where my answer is `nextcloud/server`.
```bash
$ (
  export OPENAI_API_KEY=[INSERT KEY HERE e.g. sk-...] &&
  git clone https://github.com/EmilioOldenziel/vcli-agent &&
  cd vcli-agent &&
  echo "ask_agent ask me for a github repo (owner/name), \
  then fetch its latest release tag and report it back to me" |
  python3 -m vcli.llm_agent
)
Cloning into 'vcli-agent'...
remote: Enumerating objects: 50, done.
remote: Counting objects: 100% (50/50), done.
remote: Compressing objects: 100% (28/28), done.
remote: Total 50 (delta 26), reused 46 (delta 22), pack-reused 0 (from 0)
Receiving objects: 100% (50/50), 30.08 KiB | 1.20 MiB/s, done.
Resolving deltas: 100% (26/26), done.
llm ready. Type 'help' for commands.
llm> ask_agent ask me for a github repo (owner/name),   then fetch its latest release tag and report it back to me
cat AGENT.md | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-
llm> cat AGENT.md | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-
CMD: ask_human Please provide a GitHub repository in the format owner/repo (e.g., ggerganov/llama.cpp): | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-
llm> ask_human Please provide a GitHub repository in the format owner/repo (e.g., ggerganov/llama.cpp): | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-

[agent asks] Please provide a GitHub repository in the format owner/repo (e.g., ggerganov/llama.cpp):
human> nextcloud/server
CMD: curl https://api.github.com/repos/nextcloud/server/releases/latest | grep tag_name | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-
llm> curl https://api.github.com/repos/nextcloud/server/releases/latest | grep tag_name | pack | curl -X POST https://api.openai.com/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer **********' -m 300 -d @-
DONE: fetched the latest release tag for the nextcloud/server repository, which is v33.0.2.
llm>
```

Both `vcli` and `vcli.llm_agent` read stdin as the initial command when it's not a tty, so you can pipe a pipeline straight in — `vcli.llm_agent` also picks up `OPENAI_API_KEY` from the environment and builds the bearer header itself:

```bash
# demo agent: run a vcli pipeline from stdin
echo "echo hello world | upper" | python3 -m vcli
```

## Why this exists

I built vcli to understand, for myself, what an **LLM harness** actually is — stripped of frameworks, SDKs, and orchestration jargon. A harness is just a loop: the model emits text, something parses that text into a tool call, the tool runs, its output is fed back, the model gets another turn. Everything else is decoration. To make that shape impossible to hide from, I wrote it in **pure Python** with tools as ordinary functions in a dict. No eval, no subprocess, no shelling out — the sandbox *is* the Python runtime, and the set of things the model can do is exactly the set of functions you chose to register. That constraint is the whole point: a harness is not a security boundary bolted on after the fact, it is the verbs you exposed.

## The idea

`vcli` is a minimalist experiment in **pipe-native agent loops**. Instead of the usual tool-calling harness that hides the LLM behind JSON tool schemas and a driver loop, vcli treats the LLM as just another command in a Unix-style pipeline. The LLM's reply **is** the next command line; its output pipes into the next curl call; the whole "agent" is a chain of pipes you can read left to right:

```
ask_agent  →  cat AGENT.md | pack | curl(llm)  →  ask_human | pack | curl(llm)  →  curl(github) | grep | pack | curl(llm)  →  DONE
```

Every "thinking" step is a real curl POST to a chat-completions endpoint. Every "doing" step is a real command (`curl`, `grep`, `memory`, `ask_human`). There is no hidden driver — the loop is literally strings being piped between commands.

Crucially, the "commands" are **virtual**: `grep`, `sed`, `awk`, `cut`, `curl` and friends are not shell-outs to the host's binaries, they are small pure-Python functions over strings and `urllib`. Nothing the LLM writes ever becomes a subprocess, a shell invocation, or an `eval`. That means the harness does not need Docker, seccomp, namespaces, or chroot to stay bounded — it never touches the host in the first place. The only verbs that exist are the ones registered on the agent, and the sandbox is exactly that registry. The planned virtual filesystem (see *Next steps*) closes the last seam by moving `cat` and `tee` off the real disk too.

See `AGENT.md` for the full protocol; it doubles as the system prompt the LLM reads when `ask_agent` runs.

## Features

- **Decorator-based commands** — `@agent.cmd(name=..., help=...)`
- **Shared context** — `agent.context` dict for cross-command state
- **Pipes** — `echo hello | upper` threads output into the next command's args
- **Chains** — `echo a; echo b` runs commands sequentially
- **Built-in unix-style toolkit** (see `vcli/tools.py`):
  - text: `echo`, `upper`, `lower`, `grep`, `sed`, `cut`, `awk`, `head`, `tail`, `sort`, `uniq`, `wc`, `count`
  - I/O: `cat`, `curl`, `tee`, `url`, `date`
  - meta: `help`, `exit`

The point of vcli is that **this same grammar is what the LLM writes**. When `llm_agent.py` runs the agent loop, the model's reply is literally a line like the ones above, ending in a `curl` back to itself. No tool-call JSON, no hidden driver — the LLM is composing unix pipelines, and you can read what it did left to right.

## The auto-chain driver

`Agent.run` supports an optional auto-chain mode: if `context['extract_command']` is set and returns a non-empty string from a command's output, the REPL treats that string as the next command to execute — no stdin read in between. Combined with `context['unwrap']` (which preprocesses every output) and `context['max_steps']` (step budget), this is enough to turn any command whose output is itself a command into a driver for the next step. This is how `llm_agent.py` implements its LLM loop without a dedicated driver.

# The LLM agent loop

vcli talks to any OpenAI-compatible chat endpoint. No dependencies, no SDK — just `urllib`.

### Quickstart: OpenAI (zero setup)

`vcli.llm_agent` has a `__main__`: pipe an `ask_agent <question>` line into it and it reads stdin as the initial command. It also picks up `OPENAI_API_KEY` from the environment and builds the bearer header itself:

```bash
echo "ask_agent ask me for a github repo (owner/name), then fetch its latest release tag and report it back to me" \
  | OPENAI_API_KEY=sk-... python3 -m vcli.llm_agent
```

The agent context already defaults to `https://api.openai.com/v1/chat/completions` with `gpt-4o-mini`, so nothing else needs configuring. `ask_agent <question>` is the chain you hand it: on the first call it loads `AGENT.md` as the system prompt and seeds the conversation with your question, then the LLM takes over, writing its own pipelines and self-curling each turn until it replies `DONE:`.

To override the model, export `VCLI_MODEL` — or drop into a tiny `-c` snippet if you need to tweak more of the context. A simple model override via env var works out of the box:

```bash
echo "ask_agent summarize the llama.cpp README" \
  | OPENAI_API_KEY=sk-... VCLI_MODEL=gpt-4o python3 -m vcli.llm_agent
```

### Interactive mode

With no stdin piped in, `python3 -m vcli.llm_agent` drops straight into a REPL — type `ask_agent <question>` (or any vcli pipeline) at the prompt:

```bash
OPENAI_API_KEY=sk-... python3 -m vcli.llm_agent
llm> ask_agent ask me for a github repo (owner/name), then fetch its latest release tag and report it back to me
```

### Other providers (local llama.cpp, Ollama, vLLM, Together, Groq, …)

Any OpenAI-compatible endpoint works — point `VCLI_ENDPOINT` at it and set `VCLI_MODEL`. Local servers that don't need auth can skip the key entirely; hosted providers with a bearer token use `VCLI_API_KEY` instead of `OPENAI_API_KEY`:

```bash
# llama.cpp server, no auth
echo "ask_agent fetch a zen quote" \
  | VCLI_ENDPOINT=http://0.0.0.0:8080/v1/chat/completions \
    VCLI_MODEL=unsloth/Qwen3.5-9B-GGUF:Q4_K_M \
    python3 -m vcli.llm_agent

# hosted provider with a bearer token
echo "ask_agent fetch a zen quote" \
  | VCLI_API_KEY=gsk_... \
    VCLI_ENDPOINT=https://api.groq.com/openai/v1/chat/completions \
    VCLI_MODEL=llama-3.3-70b-versatile \
    python3 -m vcli.llm_agent
```

`ask_agent` emits a chain that packs a user message and POSTs it to the endpoint. On the first call it prepends `cat AGENT.md |` so the brief is installed as the system prompt; on later calls it just appends a new user turn against the running history. Either way, the LLM's reply is itself the next chain, which must end in another self-curl to keep the loop alive. The loop terminates when:

- the model replies `DONE: ...`, or
- the chain does not end in a self-curl, or
- an HTTP/parse error occurs, or
- `context['max_steps']` is reached.

The LLM's allowed toolset during the loop is restricted to: `curl`, `pack`, `grep`, `memory`, `ask_human`, `echo`, `cat`, `sed`, `head`, `tail`, `cut`, `awk`, `wc`, `sort`, `uniq`, `tee`, `url`, `date`, `help`. Any other command in a pipeline stage causes the whole chain to be rejected and the error fed back to the model as its next input.

# Next steps: a virtual filesystem

The natural extension of the "sandbox = the functions you registered" idea is a **virtual filesystem**, implemented in the same fully-Pythonic way. Today `cat` and `tee` touch the real disk; the next step is to introduce an in-process filesystem — a plain Python dict (or a small tree of dicts) mapping paths to contents — and rebuild the familiar filesystem tools on top of it.

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

`cat` and `tee` would then be redirected to read from and write to the vfs instead of the host disk, making the whole agent loop runnable with zero filesystem side effects. A session could start by seeding the vfs from a real directory (`vfs.load("./sample_project")`) or from an in-memory fixture, and the LLM would explore it with the same pipelines it already writes:

```
llm> tree / | head -n 20
llm> find /docs -name "*.md" | head -n 5 | cat | grep TODO
llm> glob "**/*.py" | wc -l
```

Because each of these is just a function over a dict, the implementation stays in the same register as the rest of vcli: a few dozen lines per tool, no external dependencies, and the harness boundary remains obvious — if a path isn't in the dict, the model cannot reach it.

Beyond ergonomics, the vfs **tightens the perimeter**. Today `cat` and `tee` are the last tools in the harness that touch real host state, which means the capability-confinement property has a seam: a model that knows a real path can read it, and a model that controls `tee`'s target can write to it. Moving both onto the vfs closes that seam — the only filesystem the model can observe or mutate is the dict the vfs module populated. Combined with the existing no-shell, no-eval, no-subprocess invariants, this is what lets the harness be "just a Python process" on any OS without needing Docker, seccomp, namespaces, or chroot: there is simply no code path from model output to the host filesystem.

## Writing tools as commands

A command is a function that takes a list of string args and returns a string. When piped, the upstream output is split into lines and appended to `args`:

```python
@agent.cmd(name="reverse", help="Reverse each line")
def reverse(args):
    return "\n".join(line[::-1] for line in args)
```

Return `None` or an empty string to suppress output. Raise `SystemExit` to exit the REPL.

## Example sessions

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
demo> cat /etc/hosts | grep localhost | wc -l
2

demo> cat data.tsv | cut -f 1,3 | sort -u | head -n 5
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
demo> cat access.log | awk '{print $1}' | sort | uniq -c | sort -rn | head -n 3
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
demo> date; cat TODO.md | head -n 3; echo --- ; cat TODO.md | wc -l
2026-04-05T14:56:05+02:00
# TODO
- ship v0.2
- write docs
---
27
```