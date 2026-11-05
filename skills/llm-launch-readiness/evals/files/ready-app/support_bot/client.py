"""Shared model client. Limits are set here so that every call gets them."""
import os

import anthropic

MODEL = os.environ["REPLY_MODEL"]
FALLBACK_MODEL = os.environ["REPLY_FALLBACK_MODEL"]
MAX_OUTPUT_TOKENS = 600

# The SDK reads the key from the ANTHROPIC_API_KEY environment variable.
client = anthropic.Anthropic(timeout=20.0, max_retries=3)
