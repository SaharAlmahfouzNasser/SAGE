# Modified
from __future__ import annotations

import functools
import time
from pathlib import Path
from typing import Annotated, Optional, Sequence, Union

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.base import TerminationCondition, TerminatedException
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, StopMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient


class UserApprovalTermination(TerminationCondition):
    """Terminate when the user sends an approval keyword (case-insensitive).

    Recognises: approve, approved, yes, ok, okay, confirm, confirmed, lgtm,
    accept, accepted, good, pass, proceed, continue
    """

    _APPROVAL_KEYWORDS = {
        "approve", "approved", "yes", "ok", "okay", "confirm", "confirmed",
        "lgtm", "accept", "accepted", "good", "pass", "proceed", "continue",
    }

    def __init__(self, sources: Sequence[str] | None = None) -> None:
        self._terminated = False
        self._sources = sources

    @property
    def terminated(self) -> bool:
        return self._terminated

    async def __call__(self, messages: Sequence[BaseAgentEvent | BaseChatMessage]) -> StopMessage | None:
        if self._terminated:
            raise TerminatedException("Termination condition has already been reached")
        for message in messages:
            if self._sources is not None and message.source not in self._sources:
                continue
            content = message.to_text().strip().lower()
            # Check if the entire message is an approval keyword,
            # or if any approval keyword appears in the message
            if content in self._APPROVAL_KEYWORDS or any(kw in content for kw in self._APPROVAL_KEYWORDS):
                self._terminated = True
                return StopMessage(
                    content="User approved the hypothesis.", source="UserApprovalTermination"
                )
        return None

    async def reset(self) -> None:
        self._terminated = False

from HypothesisGeneration.agents import (
    create_coding_tasks_agent,
    create_hypothesis_expansion_agent,
    create_ontologist,
    create_path_generation_agent,
    SINGLE_PATH_PROMPT,
    create_scientist,
    create_summary_agent,
    create_multi_critic_coordinator,
    create_explainability_agent,
)
from HypothesisGeneration.domains import DomainConfig, BREAST_CANCER, BLADDER_CANCER

from HypothesisGeneration.modifiedAutogen.console import CustomConsole
from HypothesisGeneration.path_evaluation import (
    evaluate_paths_logic,
    evaluate_paths_relevance,
    evaluate_paths_novelty,
    evaluate_paths_surprise,
    compute_weighted_total_score,
    sort_paths_by_score,
)
from HypothesisGeneration.utils import (
    generate_path_from_sentence,
    generate_path,
    semanticscholar_search,
    find_paths_to_label,
    get_nodes_by_label_with_fallback,
    CATEGORY_WORDS,
    INVALID_LABELS,
)


def _format_evaluated_paths(
    top_paths: list[dict],
    total_found: int,
    source_entity: str,
    target_type: str,
    weights: dict[str, float],
) -> str:
    """Format evaluated and ranked paths into a string for the ontologist."""
    lines = []

    weight_str = " + ".join(f"{k.capitalize()}({v})" for k, v in weights.items())
    lines.append(
        f"✅ Found {total_found} paths from '{source_entity}' to '{target_type}'"
    )
    lines.append(f"Evaluated and ranked by: {weight_str}")
    lines.append("")
    lines.append(f"Top {len(top_paths)} Paths (sorted by total score):")
    lines.append("")

    for path_info in top_paths:
        num = path_info["number"]
        target = path_info["target"]
        length = path_info["length"]
        total_score = path_info.get("weighted_total_score", 0.0)
        path_str = path_info["path_string"]

        logic = path_info.get("logic_score", 0.0)
        relevance = path_info.get("relevance_score", 0.0)
        novelty = path_info.get("novelty_score", 0.0)
        surprise = path_info.get("surprise_score", 0.0)

        lines.append(
            f"{num}. Path to '{target}' (length: {length}) ⭐ Total: {total_score:.3f}"
        )
        lines.append(f"   {path_str}")
        lines.append(
            f"   Logic: {logic:.3f} | Relevance: {relevance:.3f} | "
            f"Novelty: {novelty:.3f} | Surprise: {surprise:.3f}"
        )
        lines.append("")

    num_to_analyze = min(10, len(top_paths))
    lines.append(f"{'=' * 70}")
    lines.append("📋 INSTRUCTIONS FOR ONTOLOGIST:")
    lines.append(f"{'=' * 70}")
    lines.append(f"You have received {len(top_paths)} ranked candidate paths above.\n")
    lines.append(
        f"Task: Select and analyze the TOP {num_to_analyze} most scientifically interesting paths.\n"
    )
    lines.append("Required Format:")
    lines.append(
        f"1. PATH SELECTION: List all {num_to_analyze} selected paths with brief selection rationale"
    )
    lines.append(
        f"2. DETAILED ANALYSIS: For each of the {num_to_analyze} paths, provide:"
    )
    lines.append("   - Key Definitions (2-4 sentences per term)")
    lines.append("   - Relationship Analysis (2-3 sentences per relationship)")
    lines.append("   - Clinical/Research Significance (2-3 sentences)")
    lines.append("\nSelection Criteria (prioritize paths with):")
    lines.append("  • High total score (Logic + Relevance + Novelty + Surprise)")
    lines.append("  • Novel biological connections")
    lines.append("  • Clear therapeutic or biomarker potential")
    lines.append("  • Strong clinical relevance")
    lines.append("  • Diversity across different mechanisms")
    lines.append(f"\nNote: Ensure all {num_to_analyze} paths are analyzed in detail.")
    lines.append(f"{'=' * 70}\n")

    return "\n".join(lines)


class SciAgentsGroup:
    def __init__(
        self,
        G,
        embedding_tokenizer,
        embedding_model,
        node_embeddings,
        model_clients=None,
        llm_client=None,
        use_llm_validation=True,
        # target_domain: str = "bladder cancer",
        # path_eval_top_n: int = 10,
        # path_eval_weights: Optional[dict] = None,
        domain: DomainConfig = BLADDER_CANCER,
    ):
        self.G = G
        self.embedding_tokenizer = embedding_tokenizer
        self.embedding_model = embedding_model
        self.node_embeddings = node_embeddings
        self.llm_client = llm_client
        self.use_llm_validation = use_llm_validation
        # self.target_domain = target_domain
        # self.path_eval_top_n = path_eval_top_n
        # self.path_eval_weights = path_eval_weights or {
        #     "logic": 0.2,
        #     "relevance": 0.2,
        #     "novelty": 0.3,
        #     "surprise": 0.3,
        # }
        self.domain = domain
        self._user_query = ""

        # ✅ Add validation
        print(f"[INIT] llm_client type: {type(self.llm_client)}")
        print(f"[INIT] use_llm_validation: {self.use_llm_validation}")

        # Check before defining find_related_entities
        if use_llm_validation and llm_client is None:
            print("⚠️ Warning: use_llm_validation=True but llm_client=None")
            print("   Setting use_llm_validation=False")
            self.use_llm_validation = False
        
        if model_clients is None:
            model_clients = {}
        self.model_clients = model_clients
        
        def mc(agent_name):
            return self.model_clients.get(agent_name)
        
        
        # ==================================================================================
        # TOOL 1: find_related_entities - Find related entities with path evaluation
        # ==================================================================================
        # Capture references via closure
        pipeline = self

        def find_related_entities(
            source_entity: str,
            target_type: str,
            max_results: int = 30,
        ) -> str:
            """Find entities of a specific TYPE that are related to a SOURCE entity through the knowledge graph.

            Returns paths ranked by a composite score (Logic, Relevance, Novelty, Surprise).

            Args:
                source_entity: A SPECIFIC entity name (e.g., "BRCA1", "lung cancer", "metformin")
                target_type: The TYPE of entities to find (e.g., "Gene", "Disease", "Drug", "Protein")
                max_results: Maximum number of paths to return (default: 30)

            Example Usage:
                find_related_entities(
                    source_entity="tertiary lymphoid structures",
                    target_type="Gene",
                    max_results=30
                )
            """
            # --- Input validation ---
            if source_entity.lower().strip() in CATEGORY_WORDS:
                return (
                    f"❌ ERROR: '{source_entity}' is a CATEGORY, not a specific entity.\n\n"
                    f"Please use a SPECIFIC entity name as source_entity."
                )
            if target_type.lower() in INVALID_LABELS:
                return (
                    f"❌ ERROR: '{target_type}' is not a valid node type in the graph.\n\n"
                    f"Valid node types are: Gene, Disease, Protein, Drug, Compound, CellType, Process, Metabolite"
                )

            # Interactive: let the user choose how many nodes to sample
            all_target_nodes = get_nodes_by_label_with_fallback(
                pipeline.G, target_type, label_field="type"
            )
            total_available = len(all_target_nodes)

            print(f"\n{'='*60}")
            print(f"  Source entity : {source_entity}")
            print(f"  Target type   : {target_type}")
            print(f"  Available nodes of type '{target_type}': {total_available}")
            print(f"{'='*60}")

            try:
                user_input = input(
                    f"How many '{target_type}' nodes to sample? "
                    f"(Enter a number, or press Enter for default {max_results}): "
                ).strip()
                if user_input:
                    max_results = int(user_input)
                    print(f"  -> Using max_results = {max_results}")
                else:
                    print(f"  -> Using default max_results = {max_results}")
            except (ValueError, EOFError):
                print(f"  -> Invalid input, using default max_results = {max_results}")

            try:
                # --- 1. Find paths using structured data ---
                paths_dict, summary = find_paths_to_label(
                    pipeline.G,
                    pipeline.embedding_tokenizer,
                    pipeline.embedding_model,
                    pipeline.node_embeddings,
                    source_keyword=source_entity,
                    target_label=target_type,
                    max_targets=max_results,
                    shortest_path=True,
                    verbatim=True,
                    validate_nodes=False,
                )

                if not paths_dict:
                    return (
                        f"No paths found from '{source_entity}' to nodes of type '{target_type}'.\n\n"
                        f"Total nodes found: {summary.get('total_found', 0)}\n"
                        f"Suggestion: Try different entity names or check available node types."
                    )

                # --- 2. Convert to evaluation format ---
                sorted_items = sorted(paths_dict.items(), key=lambda x: x[1]["path_length"])
                paths = []
                for i, (target, info) in enumerate(sorted_items, 1):
                    paths.append({
                        "number": i,
                        "target": target,
                        "length": info["path_length"],
                        "path_string": info["path_string"],
                    })

                # --- 3. Run 4-dimensional evaluation ---
                user_query = pipeline._user_query or f"{source_entity} and {target_type}"

                paths, _ = evaluate_paths_logic(
                    user_query=user_query,
                    paths=paths,
                    model_name="all-MiniLM-L6-v2",
                )
                paths = evaluate_paths_relevance(
                    paths=paths,
                    target_domain=pipeline.domain.disease,
                    node_embeddings=pipeline.node_embeddings,
                    embedding_tokenizer=pipeline.embedding_tokenizer,
                    embedding_model=pipeline.embedding_model,
                )
                paths = evaluate_paths_novelty(
                    paths=paths,
                    G=pipeline.G,
                    node_embeddings=pipeline.node_embeddings,
                )
                paths = evaluate_paths_surprise(
                    paths=paths,
                    G=pipeline.G,
                )

                # --- 4. Compute weighted total score ---
                _weights = getattr(pipeline, "path_eval_weights", {
                    "logic": 0.2, "relevance": 0.2, "novelty": 0.3, "surprise": 0.3,
                })
                _top_n = getattr(pipeline, "path_eval_top_n", 10)
                paths = compute_weighted_total_score(
                    paths=paths,
                    weights=_weights,
                )

                # --- 5. Sort and select top-N ---
                paths = sort_paths_by_score(
                    paths=paths,
                    score_key="weighted_total_score",
                    reverse=True,
                )
                top_paths = paths[:_top_n]

                # --- 6. Format output ---
                return _format_evaluated_paths(
                    top_paths=top_paths,
                    total_found=len(paths),
                    source_entity=source_entity,
                    target_type=target_type,
                    weights=_weights,
                )

            except Exception as e:
                return (
                    f"❌ Error finding paths from '{source_entity}' to '{target_type}':\n"
                    f"{e}\n\n"
                    f"Error type: {type(e).__name__}"
                )


        # ==================================================================================
        # TOOL 2: generate_path_tool - Generate knowledge path (connecting multiple specific entities)
        # ==================================================================================
        def generate_path_tool(
            keyword_1: str,
            keyword_2: str,
            keyword_3: str = None,
            keyword_4: str = None,
            keyword_5: str = None,
        ) -> str:
            """Generate a knowledge path between 2-5 SPECIFIC entities.
            
            [Keep previous complete docstring - omitted here to save space]
            """
            # Call the actual generate_path function
            result = generate_path(
                G=self.G,
                embedding_tokenizer=self.embedding_tokenizer,
                embedding_model=self.embedding_model,
                node_embeddings=self.node_embeddings,
                keyword_1=keyword_1,
                keyword_2=keyword_2,
                keyword_3=keyword_3,
                keyword_4=keyword_4,
                keyword_5=keyword_5,
            )
            
            # Add ontologist instructions
            result += "\n\n" + "="*70 + "\n"
            result += "📋 INSTRUCTIONS FOR ONTOLOGIST:\n"
            result += "="*70 + "\n"
            result += "You have received ONE path for analysis.\n"
            result += "Please:\n"
            result += "1. Use the simple 'Definitions + Relationships' format\n"
            result += "2. Define each term concisely (2-4 sentences)\n"
            result += "3. Analyze each relationship briefly (2-3 sentences)\n"
            result += "4. Do NOT add extra sections\n"
            result += "="*70 + "\n"
            
            return result
        
        
        # ==================================================================================
        # CREATE AGENTS WITH BOTH TOOLS
        # ==================================================================================
        
        self.user = UserProxyAgent(name="user")

        # ✅ Path Generation Agent now has two tools!
        # self.path_generation_agent = create_path_generation_agent(
        #     model_client=mc("path_generation_agent"),
        #     tools=[
        #         find_related_entities,  # Tool 1: Find related entities
        #         generate_path_tool,     # Tool 2: Generate path
        #     ],
        # )

        bound_generate_path_from_sentence = functools.partial(
            generate_path_from_sentence, self.G, self.embedding_tokenizer, self.embedding_model, self.node_embeddings
        )
        self.path_generation_agent = create_path_generation_agent(
            model_client=mc("path_generation_agent"),
            tools=[bound_generate_path_from_sentence],
            system_message=SINGLE_PATH_PROMPT,
        )

        # 2) Ontologist
        self.ontologist = create_ontologist(
            model_client=mc("ontologist"),
        )

        # 3) Hypothesis crafting team
        self.scientist = create_scientist(domain=domain)
        self.hypothesis_expansion_agent = create_hypothesis_expansion_agent()
        self.multi_critic_coordinator = create_multi_critic_coordinator(
            base_model="gpt-5-mini",
            literature_sources=["semantic_scholar", "pubmed"]
        )

        # Termination conditions
        # Inner loop: ends when critic approves the hypothesis
        inner_termination = TextMentionTermination("The hypothesis is approved.")
        # Outer loop: ends when user types any approval keyword
        # (approve/approved/yes/ok/okay/confirm/lgtm/accept/good/pass/proceed/
        #  continue -- case-insensitive)
        user_approval_termination = UserApprovalTermination(sources=["user"])
        self.hypothesis_team = RoundRobinGroupChat(
            [
                RoundRobinGroupChat(
                    [
                        self.scientist,
                        self.hypothesis_expansion_agent,
                        self.multi_critic_coordinator
                    ],
                    termination_condition=inner_termination,
                    max_turns=3,
                ),
                self.user,
            ],
            termination_condition=user_approval_termination,
            max_turns=3,
            name="hypothesis_team",
        )

        # 5) Explainability agent
        self.explainability_agent = create_explainability_agent(
            model_client=mc("explainability_index_agent"),
        )

        # 6) Coding tasks agent
        self.coding_tasks_agent = create_coding_tasks_agent(domain=domain,
            model_client=mc("coding_tasks_agent"),
        )

        # 7) Summary agent
        self.summary_agent = create_summary_agent(
            model_client=mc("summary_agent"),
        )

        # -------------------- TEAM Configuration --------------------
        agents = [
            self.path_generation_agent,
            self.ontologist,
            self.hypothesis_team,
            self.explainability_agent,
            self.coding_tasks_agent,
            self.summary_agent,
        ]

        # Termination
        text_mention_termination = TextMentionTermination("TERMINATE")

        self.sci_agents_team = RoundRobinGroupChat(
            agents,
            termination_condition=text_mention_termination,
            max_turns=10,
        )

    # async def run_with_console(
    #     self,
    #     message: str = "I would like to explore research ideas between: bladder cancer and diabetes.",
    #     save_to: str | Path | None = None,
    # ):
    #     """Render the run in a nice CLI console, and optionally save to a file."""
    #     self._user_query = message  # Store for path evaluation Logic scoring

    #     await self.sci_agents_team.reset()
    #     await CustomConsole(self.sci_agents_team.run_stream(task=message), output_stats=False, file_path=save_to)

    async def run_with_console(
        self,
        message: str = "I would like to explore research ideas between: bladder cancer and diabetes.",
        save_to: str | Path | None = None,
    ):
        """Render the run, track overall token usage, and runtime excluding user wait."""
        self._user_query = message

        await self.sci_agents_team.reset()

        prompt_tokens = 0
        completion_tokens = 0

        start_wall = time.perf_counter()
        last_time = start_wall
        active_time = 0.0
        waiting_for_user = False

        async def tracked_stream():
            nonlocal prompt_tokens, completion_tokens
            nonlocal last_time, active_time, waiting_for_user

            async for event in self.sci_agents_team.run_stream(task=message):
                now = time.perf_counter()
                delta = now - last_time

                if not waiting_for_user:
                    active_time += delta

                last_time = now

                source = getattr(event, "source", "")
                waiting_for_user = source == "user"

                usage = getattr(event, "models_usage", None)

                if usage is not None:
                    prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens += getattr(usage, "completion_tokens", 0) or 0

                yield event

        await CustomConsole(
            tracked_stream(),
            output_stats=False,
            file_path=save_to,
        )

        wall_time = time.perf_counter() - start_wall
        total_tokens = prompt_tokens + completion_tokens
        idle_time = wall_time - active_time

        print("\n========== RUN METRICS ==========")
        print(f"Prompt tokens:        {prompt_tokens}")
        print(f"Completion tokens:    {completion_tokens}")
        print(f"Total tokens:         {total_tokens}")
        print(f"Wall time:            {wall_time:.2f}s")
        print(f"Active runtime:       {active_time:.2f}s")
        print(f"User wait/idle time:  {idle_time:.2f}s")
        print("=================================\n")

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "wall_time": wall_time,
            "active_time": active_time,
            "idle_time": idle_time,
        }