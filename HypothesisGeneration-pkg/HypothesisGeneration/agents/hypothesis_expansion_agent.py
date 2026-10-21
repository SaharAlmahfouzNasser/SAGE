"""
Hypothesis Agent Module

This module contains the hypothesis agent that expands the hypothesis aspect
of research proposals.
"""

from typing import Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

# Agent message
PROMPT = """You are an scientist with expertise in research proposal development and experimental design for biomedical research.

A peer scientist has drafted a new research proposal and he has provided the initial hypothesis to you.
You need to help him refine the proposal by expanding on it with rigorous scientific reasoning and detailed explanations. You will do this by critically assessing the original hypothesis and improving it. Perhaps this task is just beyond your skill set.

The expected output will follow this structured json format:

```json
{
  "notes": "Any preliminary thoughts you had while expanding the hypothesis.",
  "expanded_hypothesis": "Your expanded and refined hypothesis here.",
  "justification": "Scientific reasoning and explanations supporting the expanded hypothesis.",
}
```

Additional Instructions:
- Ensure that the expanded hypothesis is clear, testable, and grounded in existing scientific knowledge.
- The justification should include relevant background information and rationale for the proposed hypothesis, but should be **concise** and to the point.
- The experimental approach should outline feasible methods and techniques for testing the hypothesis.
- You must think step by step and make sure to cover all aspects of the hypothesis expansion. You can use the "notes" field to organize your thoughts before finalizing the expanded hypothesis.
- Do not add any introductory phrases or concluding remarks outside the specified json format.
"""

# Agent description
DESCRIPTION = 'Expands the "hypothesis" aspect of the research proposal crafted by the "scientist"'


def create_agent(model_client: Optional[OpenAIChatCompletionClient] = None) -> AssistantAgent:
    """
    Create the hypothesis agent.

    Args:
        model_client: OpenAI client for the agent. If None, uses default.

    Returns:
        AssistantAgent: The configured hypothesis agent.
    """
    if model_client is None:
        model_client = OpenAIChatCompletionClient(model="gpt-5-nano")

    return AssistantAgent(
        "hypothesis_expansion_agent",
        model_client=model_client,
        system_message=PROMPT,
        description=DESCRIPTION,
    )
