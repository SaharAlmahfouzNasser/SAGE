"""
Agents Package Initialization
"""

from .coding_tasks_agent import create_agent as create_coding_tasks_agent
from .hypothesis_expansion_agent import create_agent as create_hypothesis_expansion_agent
from .novelty_critic_agent import create_agent as create_novelty_critic_agent
from .multi_critic_agent import create_agent as create_multi_critic_agent
from .ontologist import create_agent as create_ontologist
from .path_generation_agent import create_agent as create_path_generation_agent, SINGLE_PATH_PROMPT
from .scientist import create_agent as create_scientist
from .summary_agent import create_agent as create_summary_agent
from .explainability_agent import create_agent as create_explainability_agent

from .multi_critic_coordinator import create_multi_critic_coordinator
