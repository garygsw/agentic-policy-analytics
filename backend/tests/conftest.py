"""Settings shared by every test."""
import os

# Tests never send anything to LangSmith, even when a developer's .env switches tracing on: an environment
# variable outranks .env, and LangChain reads this one too.
os.environ["LANGSMITH_TRACING"] = "false"
