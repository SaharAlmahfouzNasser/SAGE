"""
Multi-Critic Agent Module

This module provides a multi-critic evaluation system for research proposals
using the Prover-Verifier-Judge game-theoretic debate framework.
"""

from typing import List, Optional
from .multi_critic_coordinator import create_multi_critic_coordinator


def create_agent(
    base_model: str = "gpt-5-nano",
    literature_sources: Optional[List[str]] = None,
    simulation_date: Optional[str] = None,
):
    """
    Create a multi-critic novelty evaluation agent.

    This agent coordinates three specialized critics with different personalities
    and search strategies to evaluate hypothesis novelty through a game-theoretic
    debate framework.

    Args:
        base_model: Base model to use for all critics (default: "gpt-5-mini")
        literature_sources: List of literature search sources to use.
            If None, uses default sources:
            ["semantic_scholar", "pubmed", "arxiv", "biorxiv", "openalex", "europepmc", "crossref"]
        simulation_date: Optional date for "Time Travel" backtesting mode, format "YYYY-MM".
            If set, the system will:
            1. Filter out papers published after this date
            2. Instruct agents to ignore knowledge from after this date
            Example: simulation_date="2019-06" to test pre-July 2019 era

    Returns:
        The configured multi-critic agent (MultiCriticCoordinatorAgent).

    Example:
        >>> # Create default multi-critic agent
        >>> agent = create_agent()

        >>> # Create with specific model and time travel mode
        >>> agent = create_agent(
        ...     base_model="gpt-4o-mini",
        ...     simulation_date="2020-01"
        ... )
    """
    return create_multi_critic_coordinator(
        base_model=base_model,
        literature_sources=literature_sources,
        simulation_date=simulation_date
    )
