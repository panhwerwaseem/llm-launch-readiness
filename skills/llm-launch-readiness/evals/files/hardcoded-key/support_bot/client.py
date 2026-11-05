"""Shared model client. Limits are set here so that every call gets them."""
import os

import anthropic

MODEL = os.environ["REPLY_MODEL"]
FALLBACK_MODEL = os.environ["REPLY_FALLBACK_MODEL"]
MAX_OUTPUT_TOKENS = 600

# TODO: move to the secret store before launch.
client = anthropic.Anthropic(api_key="sk-FAKE-not-a-real-key-0000", timeout=20.0, max_retries=3)
