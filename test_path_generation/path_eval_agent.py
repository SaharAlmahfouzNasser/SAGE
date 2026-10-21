"""
Path Evaluation Agent Module

This module contains the path evaluation agent that evaluates top-scoring paths
using LLM-based assessment across four dimensions: Logic, Relevance, Novelty, and Surprise.
"""

from typing import Optional, List, Dict, Any

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


# Agent prompt
PROMPT = """You are an expert biomedical path evaluator. Evaluate knowledge graph paths across four dimensions to identify promising research connections.

====================================================================================
EVALUATION DIMENSIONS:
====================================================================================

### 1. LOGIC (Semantic Coherence) - 0.0 to 1.0
Measures how well the path aligns with the user's query.
- High (0.8-1.0): Direct, logical connections addressing the query
- Medium (0.5-0.7): Somewhat related with gaps
- Low (0.0-0.4): Semantic breaks or implausible connections

### 2. RELEVANCE (Domain Alignment) - 0.0 to 1.0
Measures relevance to the target research domain.
- High (0.8-1.0): Highly relevant, directly contributes to target domain
- Medium (0.5-0.7): Moderate relevance
- Low (0.0-0.4): Minimal relevance

### 3. NOVELTY (Entity Rarity) - 0.0 to 1.0
Measures how rare/understudied the entities are.
- High (0.8-1.0): Rare entities with novel discovery potential
- Medium (0.5-0.7): Mix of common and rare entities
- Low (0.0-0.4): Only well-known entities

### 4. SURPRISE (Cross-Domain Connections) - 0.0 to 1.0
Measures unexpected cross-domain connections.
- High (0.8-1.0): Highly surprising cross-domain links (e.g., drug → microbiome → disease)
- Medium (0.5-0.7): Moderate cross-domain connections
- Low (0.0-0.4): Predictable, single-domain connections

====================================================================================
OUTPUT FORMAT:
====================================================================================

For EACH path:

### PATH [NUMBER]: [Target Entity]

**Path:** [Full path string]
**Path Length:** [Number of entities]

#### Evaluation Scores:

**Logic Score: [0.0-1.0]**
- **Justification:** [2-3 sentences on semantic coherence and query alignment]

**Relevance Score: [0.0-1.0]**
- **Justification:** [2-3 sentences on domain alignment]

**Novelty Score: [0.0-1.0]**
- **Justification:** [2-3 sentences on entity rarity and new insights potential]

**Surprise Score: [0.0-1.0]**
- **Justification:** [2-3 sentences on cross-domain connections]

**Overall Assessment:**
- **Strengths:** [2-3 sentences]
- **Weaknesses:** [1-2 sentences]
- **Research Potential:** [1-2 sentences]

---

After all paths:

### COMPARATIVE ANALYSIS:

**Best Overall Path:** Path [N] - [Brief reason]

**Most Novel Path:** Path [N] - [Brief reason]

**Most Surprising Path:** Path [N] - [Brief reason]

**Most Relevant Path:** Path [N] - [Brief reason]

**Recommendation:**
[2-3 sentences on recommended paths for further research]

====================================================================================
GUIDELINES:
====================================================================================

- Be critical but fair; don't inflate scores
- Consider biological plausibility
- Balance innovation with research feasibility
- Provide specific, concrete justifications
- Compare paths to calibrate scores
- Focus on research value
- No introductory phrases outside the format
"""

# Agent description
DESCRIPTION = "Expert biomedical path evaluator who assesses knowledge graph paths across Logic, Relevance, Novelty, and Surprise dimensions"


def create_agent(
    model_client: Optional[OpenAIChatCompletionClient] = None,
) -> AssistantAgent:
    """
    Create the path evaluation agent.

    Args:
        model_client: OpenAI client for the agent. If None, uses default.

    Returns:
        AssistantAgent: The configured path evaluation agent.
    """
    if model_client is None:
        model_client = OpenAIChatCompletionClient(model="gpt-4o")

    return AssistantAgent(
        "path_eval_agent",
        model_client=model_client,
        system_message=PROMPT,
        description=DESCRIPTION,
    )


def format_paths_for_evaluation(
    paths: List[Dict],
    user_query: str,
    target_domain: str = "bladder cancer",
    top_n: int = 5
) -> str:
    """
    Format top paths for LLM evaluation.
    
    Args:
        paths: List of path dictionaries (should be sorted by score)
        user_query: Original user query
        target_domain: Target research domain
        top_n: Number of top paths to evaluate (default: 5)
        
    Returns:
        Formatted string for LLM evaluation
    """
    # Select top N paths
    top_paths = paths[:top_n]
    
    # Build formatted message
    message_parts = [
        "=" * 80,
        "PATH EVALUATION REQUEST",
        "=" * 80,
        "",
        f"**User Query:** {user_query}",
        f"**Target Domain:** {target_domain}",
        "",
        f"**Top {len(top_paths)} Paths for Evaluation:**",
        "",
    ]
    
    for idx, path in enumerate(top_paths, start=1):
        path_number = path.get('number', idx)
        target = path.get('target', 'Unknown')
        path_string = path.get('path_string', '')
        path_length = path.get('length', 0)
        
        # Get current scores
        logic_score = path.get('logic_score', 0.0)
        relevance_score = path.get('relevance_score', 0.0)
        novelty_score = path.get('novelty_score', 0.0)
        surprise_score = path.get('surprise_score', 0.0)
        total_score = path.get('weighted_total_score', 0.0)
        
        message_parts.extend([
            f"---",
            f"**PATH {path_number}:**",
            f"Target: {target}",
            f"Path: {path_string}",
            f"Length: {path_length} entities",
            f"Current Scores:",
            f"  - Logic: {logic_score:.3f}",
            f"  - Relevance: {relevance_score:.3f}",
            f"  - Novelty: {novelty_score:.3f}",
            f"  - Surprise: {surprise_score:.3f}",
            f"  - Total: {total_score:.3f}",
            "",
        ])
    
    message_parts.extend([
        "=" * 80,
        "",
        "Please evaluate each path across the four dimensions (Logic, Relevance, Novelty, Surprise) and provide detailed justifications.",
        "Use the output format specified in your instructions.",
    ])
    
    return "\n".join(message_parts)


async def evaluate_paths_with_llm(
    agent: AssistantAgent,
    paths: List[Dict],
    user_query: str,
    target_domain: str = "bladder cancer",
    top_n: int = 5
) -> str:
    """
    Use the path evaluation agent to evaluate top paths.
    
    Args:
        agent: The path evaluation agent
        paths: List of path dictionaries (should be sorted by score)
        user_query: Original user query
        target_domain: Target research domain
        top_n: Number of top paths to evaluate (default: 5)
        
    Returns:
        Agent's evaluation response as string
    """
    # Format paths for evaluation
    evaluation_request = format_paths_for_evaluation(
        paths=paths,
        user_query=user_query,
        target_domain=target_domain,
        top_n=top_n
    )
    
    # Send message to agent and get response
    # Note: on_messages requires cancellation_token parameter
    from autogen_agentchat.messages import TextMessage
    
    messages = [TextMessage(content=evaluation_request, source="user")]
    response = await agent.on_messages(messages, cancellation_token=None)
    
    # Extract text content from response
    if hasattr(response, 'chat_message'):
        # Response object with chat_message
        chat_message = response.chat_message
        if hasattr(chat_message, 'content'):
            return str(chat_message.content)
        elif hasattr(chat_message, 'to_text'):
            return chat_message.to_text()
    elif hasattr(response, 'messages') and response.messages:
        # Get the last message (agent's response)
        last_message = response.messages[-1]
        if hasattr(last_message, 'content'):
            return str(last_message.content)
        elif hasattr(last_message, 'to_text'):
            return last_message.to_text()
    
    # Fallback: convert response to string
    return str(response)

