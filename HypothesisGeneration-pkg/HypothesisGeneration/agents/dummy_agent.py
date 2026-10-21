"""
Hypothesis Agent Module

This module contains the hypothesis agent that expands the hypothesis aspect
of research proposals.
"""

from typing import Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

# Agent message
PROMPT = """You are a basic AI assistant for testing purposes."""

# Agent description
DESCRIPTION = "Dummy basic agent for testing purposes"


def create_agent(model_client: Optional[OpenAIChatCompletionClient] = None) -> AssistantAgent:
    """
    Dummy basic agent for testing purposes.
    Args:
        model_client: OpenAI client for the agent. If None, uses default.

    Returns:
        AssistantAgent: The configured hypothesis agent.
    """

    if model_client is None:
        model_client = OpenAIChatCompletionClient(model="gpt-4.1-nano")

    return AssistantAgent(
        "dummy_agent",
        model_client=model_client,
        system_message=PROMPT,
        description=DESCRIPTION,
    )
