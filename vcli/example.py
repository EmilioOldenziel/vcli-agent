"""Example: a small virtual CLI with a few demo commands."""

from .vcli import Agent

agent = Agent(name="demo")


@agent.cmd(name="echo", help="Echo back arguments")
def echo(args):
    return " ".join(args)


@agent.cmd(name="set", help="Set a context variable: set KEY VALUE")
def set_var(args):
    if len(args) < 2:
        return "usage: set KEY VALUE"
    agent.context[args[0]] = " ".join(args[1:])
    return f"{args[0]} = {agent.context[args[0]]}"


@agent.cmd(name="get", help="Get a context variable: get KEY")
def get_var(args):
    if not args:
        return "\n".join(f"{k} = {v}" for k, v in agent.context.items()) or "(empty)"
    return agent.context.get(args[0], f"'{args[0]}' not set")


@agent.cmd(name="calc", help="Evaluate a simple math expression")
def calc(args):
    expr = " ".join(args)
    if not all(c in "0123456789+-*/.() " for c in expr):
        return "only basic math allowed"
    return eval(expr)


if __name__ == "__main__":
    agent.run()
