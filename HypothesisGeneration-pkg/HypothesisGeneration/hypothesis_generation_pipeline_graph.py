# Modified
from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Optional, Union

from autogen_agentchat.agents import (
    MessageFilterAgent,
    MessageFilterConfig,
    PerSourceFilter,
    UserProxyAgent,
)
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import (
    DiGraphBuilder,
    GraphFlow,
    RoundRobinGroupChat,
)
from autogen_ext.models.openai import OpenAIChatCompletionClient

from HypothesisGeneration.agents import (
    create_coding_tasks_agent,
    create_hypothesis_expansion_agent,
    create_novelty_critic_agent,
    create_ontologist,
    create_path_generation_agent,
    create_scientist,
    create_summary_agent,
)
from HypothesisGeneration.modifiedAutogen.console import CustomConsole
from HypothesisGeneration.report_generator import main as generate_report
from HypothesisGeneration.path_evaluation import (
    evaluate_paths_logic,
    evaluate_paths_relevance,
    evaluate_paths_novelty,
    evaluate_paths_surprise,
    compute_weighted_total_score,
    sort_paths_by_score,
)
from HypothesisGeneration.utils import (
    find_paths_to_label,
    generate_path,
    generate_path_from_sentence,
    semanticscholar_search,
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

    # Header
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

    # Instructions for ontologist
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


class SciAgentsGraphFlow:
    def __init__(
        self,
        G,
        embedding_tokenizer,
        embedding_model,
        node_embeddings,
        model_clients: Optional[dict[str, OpenAIChatCompletionClient]] = None,
        target_domain: str = "bladder cancer",
        path_eval_top_n: int = 10,
        path_eval_weights: Optional[dict[str, float]] = None,
    ):
        self.G = G
        self.embedding_tokenizer = embedding_tokenizer
        self.embedding_model = embedding_model
        self.node_embeddings = node_embeddings
        self.target_domain = target_domain
        self.path_eval_top_n = path_eval_top_n
        self.path_eval_weights = path_eval_weights or {
            "logic": 0.2,
            "relevance": 0.2,
            "novelty": 0.3,
            "surprise": 0.3,
        }
        self._user_query = ""  # Will be set in run_with_console

        if model_clients is None:
            model_clients = {}

        # helper: pick a client by name, else default with temperature=0
        def mc(key: str):
            return model_clients.get(key) or None

        # -------------------- AGENTS --------------------
        self.user = UserProxyAgent(name="user")

        # 1) Path generation agent
        # Create a partial function to pass the graph and embeddings to the generate_path function.
        bound_generate_path = functools.partial(
            generate_path, self.G, self.embedding_tokenizer, self.embedding_model, self.node_embeddings
        )
        bound_generate_path_from_sentence = functools.partial(
            generate_path_from_sentence, self.G, self.embedding_tokenizer, self.embedding_model, self.node_embeddings
        )

        # Create find_related_entities tool with path evaluation
        find_related_entities = self._create_find_related_entities_tool()

        self.path_generation_agent = create_path_generation_agent(
            model_client=mc("path_generation_agent"),
            tools=[bound_generate_path_from_sentence, find_related_entities],
        )

        # 2) Ontologist
        self.ontologist = create_ontologist(
            model_client=mc("ontologist"),
        )
        self.filtered_ontologist = MessageFilterAgent(
            name="ontologist",
            wrapped_agent=self.ontologist,
            filter=MessageFilterConfig(
                per_source=[PerSourceFilter(source="path_generation_agent", position="last", count=1)]
            ),
        )

        # 3) Scientist
        self.scientist = create_scientist()
        self.filtered_scientist = MessageFilterAgent(
            name="scientist",
            wrapped_agent=self.scientist,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="path_generation_agent", position="last", count=1),
                    PerSourceFilter(source="ontologist", position="last", count=1),
                ]
            ),
        )

        # 4) Hypothesis expansion agent
        self.hypothesis_expansion_agent = create_hypothesis_expansion_agent()
        self.filtered_hypothesis_expansion_agent = MessageFilterAgent(
            name="hypothesis_expansion_agent",
            wrapped_agent=self.hypothesis_expansion_agent,
            filter=MessageFilterConfig(per_source=[PerSourceFilter(source="scientist", position="last", count=1)]),
        )

        # 5) Novelty critic agent
        self.novelty_critic_agent = create_novelty_critic_agent(
            model_client=mc("novelty_critic_agent"),
            tools=[semanticscholar_search],
        )
        self.filtered_novelty_critic_agent = MessageFilterAgent(
            name="novelty_critic_agent",
            wrapped_agent=self.novelty_critic_agent,
            filter=MessageFilterConfig(
                per_source=[PerSourceFilter(source="hypothesis_expansion_agent", position="last", count=1)]
            ),
        )

        # 6) Coding tasks agent
        self.coding_tasks_agent = create_coding_tasks_agent(
            model_client=mc("coding_tasks_agent"),
        )
        self.filtered_coding_tasks_agent = MessageFilterAgent(
            name="coding_tasks_agent",
            wrapped_agent=self.coding_tasks_agent,
            filter=MessageFilterConfig(
                per_source=[PerSourceFilter(source="hypothesis_expansion_agent", position="last", count=1)]
            ),
        )

        # 7) Summary agent
        self.summary_agent = create_summary_agent(
            model_client=mc("summary_agent"),
        )
        self.filtered_summary_agent = MessageFilterAgent(
            name="summary_agent",
            wrapped_agent=self.summary_agent,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="hypothesis_expansion_agent", position="first", count=1),
                    PerSourceFilter(source="novelty_critic_agent", position="last", count=1),
                ]
            ),
        )

        # -------------------- TEAM Creation --------------------

        self.builder = DiGraphBuilder()
        # Define nodes
        self.builder.add_node(self.path_generation_agent)
        self.builder.add_node(self.filtered_ontologist)
        self.builder.add_node(self.filtered_scientist)
        self.builder.add_node(self.filtered_hypothesis_expansion_agent)
        self.builder.add_node(self.filtered_novelty_critic_agent)
        self.builder.add_node(self.filtered_coding_tasks_agent)
        self.builder.add_node(self.filtered_summary_agent)

        # Define edges between agents
        self.builder.add_edge(self.path_generation_agent, self.filtered_ontologist)
        self.builder.add_edge(self.filtered_ontologist, self.filtered_scientist)
        self.builder.add_edge(self.filtered_scientist, self.filtered_hypothesis_expansion_agent)
        self.builder.add_edge(
            self.filtered_hypothesis_expansion_agent,
            self.filtered_novelty_critic_agent,
            condition=lambda msg: "The hypothesis is approved." not in msg.to_model_text(),
        )
        self.builder.add_edge(
            self.filtered_novelty_critic_agent,
            self.filtered_coding_tasks_agent,
            condition=lambda msg: "The hypothesis is approved." in msg.to_model_text(),
        )
        # TODO summary agent, not currently used
        # self.builder.add_edge(self.filtered_coding_tasks_agent, self.filtered_summary_agent)

        # Entry point from user to path generation agent
        self.builder.set_entry_point(self.path_generation_agent)

        # Create the graph flow
        self.graph_flow = GraphFlow(
            participants=self.builder.get_participants(),
            graph=self.builder.build(),
            termination_condition=MaxMessageTermination(20),
        )

    def _create_find_related_entities_tool(self):
        """Create a find_related_entities tool function with integrated path evaluation."""
        # Capture references via closure
        G = self.G
        embedding_tokenizer = self.embedding_tokenizer
        embedding_model = self.embedding_model
        node_embeddings = self.node_embeddings
        pipeline = self  # for accessing _user_query, target_domain, etc.

        def find_related_entities(
            source_entity: Annotated[str, "A SPECIFIC entity name (e.g., 'bladder cancer', 'CXCL8')"],
            target_type: Annotated[str, "A node TYPE in the graph (e.g., 'Gene', 'Protein', 'Disease')"],
            max_results: Annotated[int, "Maximum number of paths to return"] = 30,
        ) -> str:
            """Find MULTIPLE paths from ONE specific entity to ALL entities of a certain TYPE.

            Returns paths ranked by a composite score (Logic, Relevance, Novelty, Surprise).
            Use this when the user query involves a specific entity and a category
            (e.g., 'bladder cancer and genes').
            """
            # --- Input validation (same as find_shortest_paths_to_label_for_agent) ---
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

            try:
                # --- 1. Find paths using the underlying function ---
                paths_dict, summary = find_paths_to_label(
                    G,
                    embedding_tokenizer,
                    embedding_model,
                    node_embeddings,
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

                # --- 2. Sort by path length and convert to evaluation format ---
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

                # Logic: semantic similarity between query and path
                paths, _ = evaluate_paths_logic(
                    user_query=user_query,
                    paths=paths,
                    model_name="all-MiniLM-L6-v2",
                )

                # Relevance: domain alignment
                paths = evaluate_paths_relevance(
                    paths=paths,
                    target_domain=pipeline.target_domain,
                    node_embeddings=node_embeddings,
                    embedding_tokenizer=embedding_tokenizer,
                    embedding_model=embedding_model,
                )

                # Novelty: entity rarity (based on graph degree)
                paths = evaluate_paths_novelty(
                    paths=paths,
                    G=G,
                    node_embeddings=node_embeddings,
                )

                # Surprise: cross-domain connections (KL divergence)
                paths = evaluate_paths_surprise(
                    paths=paths,
                    G=G,
                )

                # --- 4. Compute weighted total score ---
                paths = compute_weighted_total_score(
                    paths=paths,
                    weights=pipeline.path_eval_weights,
                )

                # --- 5. Sort by total score and select top-N ---
                paths = sort_paths_by_score(
                    paths=paths,
                    score_key="weighted_total_score",
                    reverse=True,
                )
                top_paths = paths[: pipeline.path_eval_top_n]

                # --- 6. Format output ---
                return _format_evaluated_paths(
                    top_paths=top_paths,
                    total_found=len(paths),
                    source_entity=source_entity,
                    target_type=target_type,
                    weights=pipeline.path_eval_weights,
                )

            except Exception as e:
                return (
                    f"❌ Error finding paths from '{source_entity}' to '{target_type}':\n"
                    f"{e}\n\n"
                    f"Error type: {type(e).__name__}"
                )

        return find_related_entities

    async def run_with_console(
        self,
        message: str = "I would like to explore research ideas between: bladder cancer and diabetes.",
        save_to: str | Path = "hypothesis_generation_run.txt",
    ):
        """Render the run in a nice CLI console, save the raw output to a file and export to md and PDF."""
        self._user_query = message  # Store for path evaluation Logic scoring

        await CustomConsole(self.graph_flow.run_stream(task=message), output_stats=True, file_path=save_to)
        generate_report(save_to)  # Generate MD and PDF report
