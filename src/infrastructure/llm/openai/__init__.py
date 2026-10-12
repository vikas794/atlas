"""OpenAI-compatible notes adapters.

The subpackage is named ``openai`` after the wire format it speaks, not after
the SDK. Absolute imports mean ``import openai`` from inside this package
still resolves to the installed SDK, never back to this directory.
"""

from __future__ import annotations
