import math


def compute_token_count(text: str) -> int:
    """Approximate token count (~4 chars per token, GPT-family convention)."""
    return max(1, math.ceil(len(text) / 4))
