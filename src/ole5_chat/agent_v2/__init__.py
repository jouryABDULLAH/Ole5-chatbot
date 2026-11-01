"""The new owner-routing agent.

This package is intentionally separate from ``ole5_chat.agent`` while the new
graph is being built and reviewed incrementally.
"""

from ole5_chat.agent_v2.graph import build

__all__ = ["build"]
