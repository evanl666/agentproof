"""Enable `python -m agentproof …` in addition to the `agentproof` console script."""

from agentproof.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
