"""Smallest wrapper that kicks off the LLM-driven agent chain.

Run: python -m vcli.bootstrap_example
Env: VCLI_ENDPOINT, VCLI_MODEL
"""

import os

from vcli.llm_agent import agent


def main() -> None:
    agent.context["endpoint"] = os.environ.get(
        "VCLI_ENDPOINT", "http://0.0.0.0:8080/v1/chat/completions"
    )
    agent.context["model"] = os.environ.get(
        "VCLI_MODEL", "unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
    )
    agent.context["max_steps"] = 6
    agent.run(initial="init")


if __name__ == "__main__":
    main()
