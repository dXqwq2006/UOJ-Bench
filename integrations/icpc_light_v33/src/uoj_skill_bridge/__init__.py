"""Isolated task bridge between UOJ-Bench and an ICPC Light agent job."""

from .runtime import BridgeContractError, execute_request

__all__ = ["BridgeContractError", "execute_request"]
