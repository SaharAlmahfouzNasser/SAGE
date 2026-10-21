"""
Path Generation Agent Test Script

This script is used to independently test the path generation agent's functionality,
without running subsequent agents such as the ontologist.
Input: User query, e.g. "Discover research idea between tertiary lymphoid structures and Gene"
Output: Path generation agent's output results
"""

import asyncio
import pickle
import sys
import re
import os
from pathlib import Path


# Add the current project path to sys.path to ensure local modules are used
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "HypothesisGeneration"))
# Add test directory to sys.path so that path_evaluation can be imported
sys.path.insert(0, str(Path(__file__).parent))

from autogen_ext.models.openai import OpenAIChatCompletionClient
from transformers import AutoModel, AutoTokenizer

from HypothesisGeneration.agents import create_path_generation_agent
from HypothesisGeneration.utils import find_shortest_paths_to_label_for_agent

# Import path evaluation module
from path_evaluation import (
    evaluate_paths_logic,
    evaluate_paths_relevance,
    evaluate_paths_novelty,
    evaluate_paths_surprise,
    compute_weighted_total_score,
    sort_paths_by_score
)

# Import path evaluation agent
from path_eval_agent import create_agent as create_path_eval_agent, evaluate_paths_with_llm


async def test_path_generation(
    user_query: str = "Discover research idea between tertiary lymphoid structures and Gene.",
    graph_path: str = "inputs/initial_graph_biomed/global_graph_1.7k.graphml",
    embeddings_path: str = "inputs/initial_graph_biomed/global_graph_1.7k.pkl",
    embedding_model_name: str = "BAAI/bge-large-en-v1.5",
    use_llm_validation: bool = True,
    show_detailed_output: bool = False,  # Whether to show detailed output
    evaluate_logic: bool = True,  # Whether to perform Logic evaluation
    # logic_threshold: float = 0.5,  # Logic evaluation threshold
    evaluate_relevance: bool = True,  # Whether to perform Relevance evaluation
    relevance_target_domain: str = "bladder cancer",  # Target domain for Relevance evaluation
    evaluate_novelty: bool = True,  # Whether to perform Novelty evaluation
    evaluate_surprise: bool = True,  # Whether to perform Surprise evaluation
    use_llm_evaluation: bool = True,  # Whether to use LLM for further evaluation
    top_n_for_llm: int = 5,  # Number of paths to send to LLM for evaluation
):
    """
    Test the path generation agent

    Args:
        user_query: User query string
        graph_path: Knowledge graph file path (.graphml)
        embeddings_path: Node embeddings file path (.pkl)
        embedding_model_name: Embedding model name
        use_llm_validation: Whether to use LLM to validate paths
    """

    import networkx as nx
    from HypothesisGeneration.utils import load_graph_with_text_as_JSON, return_giant_component_of_graph

    # ==================== 1. Load knowledge graph ====================
    # Convert relative path to absolute path relative to project root
    project_root = Path(__file__).parent.parent
    graph_file = project_root / graph_path if not Path(graph_path).is_absolute() else Path(graph_path)
    if not graph_file.exists():
        raise FileNotFoundError(f"Graph file not found: {graph_path} (resolved to: {graph_file})")

    # Load graph from graphml file
    G = load_graph_with_text_as_JSON(
        data_dir=str(graph_file.parent),
        graph_name=graph_file.name
    )
    G = return_giant_component_of_graph(G)
    G = nx.Graph(G)

    # ==================== 2. Load node embeddings ====================
    # Convert relative path to absolute path relative to project root
    embeddings_file = project_root / embeddings_path if not Path(embeddings_path).is_absolute() else Path(embeddings_path)
    if not embeddings_file.exists():
        raise FileNotFoundError(f"Embeddings file not found: {embeddings_path} (resolved to: {embeddings_file})")

    with open(embeddings_file, "rb") as f:
        node_embeddings = pickle.load(f)

    # ==================== 3. Load embedding model ====================
    embedding_tokenizer = AutoTokenizer.from_pretrained(embedding_model_name)
    embedding_model = AutoModel.from_pretrained(embedding_model_name)

    # ==================== 4. Create LLM client (for validation) ====================
    llm_client = None
    if use_llm_validation:
        llm_client = OpenAIChatCompletionClient(model="gpt-4o-mini")

    # ==================== 5. Define find_related_entities tool ====================
    def find_related_entities(
        source_entity: str,
        target_type: str,
        max_results: int = 10,
    ) -> str:
        """Tool function to find related entities"""
        result = find_shortest_paths_to_label_for_agent(
            G=G,
            embedding_tokenizer=embedding_tokenizer,
            embedding_model=embedding_model,
            node_embeddings=node_embeddings,
            source_keyword=source_entity,
            target_label=target_type,
            max_results=max_results,
            label_field="type",
            validate_nodes=False,
            use_llm_validation=use_llm_validation,
            llm_client=llm_client,
            llm_model="gpt-4o-mini",
            client_type="openai",
        )

        """ # Add ontologist instructions
        result += "\n\n" + "="*70 + "\n"
        result += "📋 INSTRUCTIONS FOR ONTOLOGIST:\n"
        result += "="*70 + "\n"

        # Check how many paths are in the result
        import re
        path_matches = re.findall(r'^\d+\. Path to', result, re.MULTILINE)
        num_paths = len(path_matches)

        if num_paths > 1:
            if num_paths <= 10:
                num_to_analyze = "5-8"
            elif num_paths <= 25:
                num_to_analyze = "10-15"
            else:
                num_to_analyze = "15-20"

            result += f"You have received {num_paths} candidate paths above.\n"
            result += "Please:\n"
            result += f"1. Review ALL {num_paths} paths carefully\n"
            result += f"2. Select and analyze the TOP {num_to_analyze} most scientifically interesting paths\n"
            result += "3. Use the 'PATH SELECTION + ANALYSIS' format from your system instructions\n\n"
            result += "Prioritize paths with:\n"
            result += "- Novel biological connections\n"
            result += "- Clear therapeutic potential\n"
            result += "- Strong clinical relevance\n"
            result += "- Mechanistic clarity\n"
        else:
            result += "You have received ONE path for analysis.\n"

        result += "="*70 + "\n"
        """

        return result

    # ==================== 6. Create path generation agent ====================
    model_client = OpenAIChatCompletionClient(model="gpt-4o")
    path_generation_agent = create_path_generation_agent(
        model_client=model_client,
        tools=[find_related_entities],
    )

    # ==================== 7. Run agent ====================
    from autogen_agentchat.messages import TextMessage

    messages = [TextMessage(content=user_query, source="user")]
    response = await path_generation_agent.on_messages(messages, cancellation_token=None)

    # ==================== 8. Extract path data (silent processing, no intermediate output) ====================

    # Store all paths
    all_paths = []

    # Extract paths from response, do not print any intermediate process
    inner_messages = getattr(response, 'inner_messages', None) or []

    for msg in inner_messages:
        try:
            # Skip non-message objects
            if not hasattr(msg, '__class__'):
                continue

            msg_class = msg.__class__.__name__

            # Process ToolCallExecutionEvent, extract path data (no printing)
            if msg_class == 'ToolCallExecutionEvent':
                content = None

                # Method 1: Get from content attribute (preferred, as debugging shows content is here)
                if hasattr(msg, 'content') and msg.content:
                    if isinstance(msg.content, str):
                        # If content is a string, check if it contains path information
                        if 'Path to' in msg.content:
                            # If it directly contains paths, use it directly
                            content = msg.content
                        elif 'FunctionExecutionResult' in msg.content:
                            # If it contains FunctionExecutionResult, try to extract
                            # Use a more robust regex to handle escape characters
                            content_match = re.search(r'content="((?:[^"\\]|\\.)*)"', msg.content)
                            if content_match:
                                content = content_match.group(1)
                                # Handle escaped characters
                                content = content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                    elif isinstance(msg.content, list):
                        for item in msg.content:
                            if isinstance(item, str) and 'Path to' in item:
                                content = item
                                break
                            elif hasattr(item, 'content') and item.content:
                                if isinstance(item.content, str) and 'Path to' in item.content:
                                    content = item.content
                                    break

                # Method 2: Get from results attribute (fallback method)
                if not content and hasattr(msg, 'results'):
                    results = msg.results
                    if isinstance(results, list):
                        for result in results:
                            # Check if result has a content attribute
                            if hasattr(result, 'content') and result.content:
                                content = result.content
                                break
                            # If result is a string (possibly serialized), try to parse
                            elif isinstance(result, str):
                                # Extract content="..." part from string
                                # Format: FunctionExecutionResult(content="...")
                                # Use a more robust regex to handle escape characters
                                content_match = re.search(r'content="((?:[^"\\]|\\.)*)"', result)
                                if content_match:
                                    # Handle escaped characters
                                    content = content_match.group(1)
                                    # Handle escaped newlines and quotes
                                    content = content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                                    if 'Path to' in content:
                                        break

                # If content was found, extract paths
                if content:
                    paths = _extract_paths_from_content(content)
                    if paths:
                        all_paths.extend(paths)
        except Exception:
            # Silently handle errors
            pass

    # ==================== 9. Path evaluation ====================

    # Perform Logic evaluation on each path
    evaluation_stats = None
    if evaluate_logic and all_paths:
        all_paths, evaluation_stats = evaluate_paths_logic(
            user_query=user_query,
            paths=all_paths,
            model_name="all-MiniLM-L6-v2",  # Sentence-BERT model
            # threshold=logic_threshold,
        )

    # Perform Relevance evaluation on each path
    if evaluate_relevance and all_paths:
        all_paths = evaluate_paths_relevance(
            paths=all_paths,
            target_domain=relevance_target_domain,
            node_embeddings=node_embeddings,
            embedding_tokenizer=embedding_tokenizer,
            embedding_model=embedding_model
        )

    # Perform Novelty evaluation on each path
    if evaluate_novelty and all_paths:
        all_paths = evaluate_paths_novelty(
            paths=all_paths,
            G=G,
            node_embeddings=node_embeddings
        )

    # Perform Surprise evaluation on each path
    if evaluate_surprise and all_paths:
        all_paths = evaluate_paths_surprise(
            paths=all_paths,
            G=G
        )

    # ==================== 9.5. Compute weighted total score ====================

    # Weight configuration: encourage innovation
    # Novelty and Surprise each account for 0.3, Logic and Relevance each account for 0.2
    weights = {
        'logic': 0.2,
        'relevance': 0.2,
        'novelty': 0.3,
        'surprise': 0.3
    }

    # Compute weighted total score for each path
    all_paths = compute_weighted_total_score(
        paths=all_paths,
        weights=weights,
        evaluate_logic=evaluate_logic,
        evaluate_relevance=evaluate_relevance,
        evaluate_novelty=evaluate_novelty,
        evaluate_surprise=evaluate_surprise
    )

    # ==================== 9.6. Sort by weighted total score ====================

    # Sort by weighted total score in descending order
    all_paths = sort_paths_by_score(
        paths=all_paths,
        score_key='weighted_total_score',
        reverse=True
    )

    # ==================== 10. Format output ====================

    # Collect output content (for saving to file)
    output_lines = []

    # Output user input
    print("---------- Input ----------")
    output_lines.append("---------- Input ----------")
    print(user_query)
    output_lines.append(user_query)
    print()
    output_lines.append("")

    # Output path list
    print("---------- Path ----------")
    output_lines.append("---------- Path ----------")
    if all_paths:
        for path_info in all_paths:
            # Get path string, ensure newlines display correctly
            path_str = path_info['path_string']
            # If path string contains literal \n, convert to actual newline characters
            if '\\n' in path_str:
                path_str = path_str.replace('\\n', '\n')

            # Formatted output: number. target node (length: value): path displayed on new line
            path_line = f"{path_info['number']}. Path to '{path_info['target']}' (length: {path_info['length']}):"
            print(path_line)
            output_lines.append(path_line)
            path_str_line = f"   {path_str}"
            print(path_str_line)
            output_lines.append(path_str_line)

            # Display Logic evaluation result (if evaluated)
            if 'logic_score' in path_info:
                logic_score = path_info['logic_score']
                logic_line = f"   Logic Score: {logic_score:.3f}"
                print(logic_line)
                output_lines.append(logic_line)

            # Display Relevance evaluation result (if evaluated)
            if 'relevance_score' in path_info:
                relevance_score = path_info['relevance_score']
                num_entities = path_info.get('relevance_num_entities', 0)
                num_found = path_info.get('relevance_num_found', 0)
                relevance_line = f"   Relevance Score: {relevance_score:.3f} (entities: {num_found}/{num_entities}, target: {relevance_target_domain})"
                print(relevance_line)
                output_lines.append(relevance_line)

            # Display Novelty evaluation result (if evaluated)
            if 'novelty_score' in path_info:
                novelty_score = path_info['novelty_score']
                num_entities = path_info.get('novelty_num_entities', 0)
                num_found = path_info.get('novelty_num_found', 0)
                novelty_line = f"   Novelty Score: {novelty_score:.3f} (entities: {num_found}/{num_entities})"
                print(novelty_line)
                output_lines.append(novelty_line)

            # Display Surprise evaluation result (if evaluated)
            if 'surprise_score' in path_info:
                surprise_score = path_info['surprise_score']
                surprise_raw = path_info.get('surprise_raw_score', 0.0)
                num_entities = path_info.get('surprise_num_entities', 0)
                num_categories = path_info.get('surprise_num_categories', 0)
                kl_div = path_info.get('surprise_kl_divergence', 0.0)
                surprise_line = f"   Surprise Score: {surprise_score:.3f} (raw KL Divergence: {kl_div:.3f}, entities: {num_entities}, categories: {num_categories})"
                print(surprise_line)
                output_lines.append(surprise_line)

            # Display weighted total score
            if 'weighted_total_score' in path_info:
                total_score = path_info['weighted_total_score']
                components = path_info.get('score_components', {})
                weights = path_info.get('score_weights', {})

                # Build weight display string
                component_strs = []
                if 'logic' in components:
                    component_strs.append(f"Logic: {weights.get('logic', 0):.1f}")
                if 'relevance' in components:
                    component_strs.append(f"Relevance: {weights.get('relevance', 0):.1f}")
                if 'novelty' in components:
                    component_strs.append(f"Novelty: {weights.get('novelty', 0):.1f}")
                if 'surprise' in components:
                    component_strs.append(f"Surprise: {weights.get('surprise', 0):.1f}")

                total_line = f"   ⭐ Total Score: {total_score:.3f}"
                print(total_line)
                output_lines.append(total_line)
                if component_strs:
                    weights_line = f"      Weights: {', '.join(component_strs)}"
                    print(weights_line)
                    output_lines.append(weights_line)

            print()
            output_lines.append("")
    else:
        print("No paths found")
        output_lines.append("No paths found")

    # ==================== 11. LLM deep evaluation (optional, after path output) ====================

    llm_evaluation_result = None
    if use_llm_evaluation and all_paths:
        try:
            # Create path evaluation agent
            eval_agent = create_path_eval_agent()

            # Evaluate the top N paths with highest scores
            llm_evaluation_result = await evaluate_paths_with_llm(
                agent=eval_agent,
                paths=all_paths,
                user_query=user_query,
                target_domain=relevance_target_domain,
                top_n=top_n_for_llm
            )

            # Output LLM evaluation results
            if llm_evaluation_result:
                separator = "=" * 80
                print(separator)
                output_lines.append("")
                output_lines.append(separator)
                title_line = f"LLM Evaluations (Top {top_n_for_llm} paths)"
                print(title_line)
                output_lines.append(title_line)
                print(separator)
                output_lines.append(separator)
                print()
                output_lines.append("")
                print(llm_evaluation_result)
                output_lines.append(llm_evaluation_result)
                print()
                output_lines.append("")
                print(separator)
                output_lines.append(separator)
        except Exception as e:
            print(f"[WARNING] LLM evaluation failed: {e}")
            import traceback
            traceback.print_exc()
            llm_evaluation_result = None

    # ==================== 12. Save output to file ====================

    # Save to test_path.txt (output_lines already collected during output)
    output_file = Path(__file__).parent / "test_path.txt"
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))
        print(f"\n[INFO] Output saved to: {output_file}")
    except Exception as e:
        print(f"\n[WARNING] Failed to save file: {e}")

    return response, all_paths


def _extract_paths_from_content(content: str) -> list:
    """
    Extract path information from tool-returned content

    Args:
        content: String content returned by the tool

    Returns:
        paths: List of paths, each element contains target, length, path_string, validation, etc.
    """
    paths = []

    if not content or not isinstance(content, str):
        return paths

    # Use regex to match path patterns
    # Pattern: "number. Path to 'target node' (length: number):\n   path string"
    # Note: path string may contain newlines, need to match until next path start or content end
    # More lenient matching pattern, allows optional emoji
    pattern = r'(\d+)\.\s+Path to\s+\'([^\']+)\'\s*(?:✅|❌)?\s*\(length:\s*(\d+)\):\s*\n\s+([^\n]+(?:\n(?!\d+\.\s+Path to)[^\n]+)*)'
    matches = re.finditer(pattern, content, re.MULTILINE)

    for match in matches:
        path_num = match.group(1)
        target = match.group(2)
        length = int(match.group(3))
        validation_status = ""  # Removed validation status from pattern as it may not exist
        # Clean path string, preserve newlines but normalize format
        path_string = match.group(4).strip()  # Fix: should be group(4) not group(5)
        # Replace multiple consecutive spaces with a single space, but preserve newline characters
        path_string = re.sub(r' +', ' ', path_string)  # Replace multiple spaces with single space
        # If path string contains literal \n, convert to actual newline characters
        if '\\n' in path_string:
            path_string = path_string.replace('\\n', '\n')

        paths.append({
            'number': int(path_num),
            'target': target,
            'length': length,
            'path_string': path_string,
            'validation': validation_status if validation_status else 'Not validated'
        })

    return paths


async def main():
    """Main function"""
    # Test default query
    await test_path_generation(
        user_query="Discover research idea between tertiary lymphoid structures and Gene.",
    )

    # Can test other queries
    # await test_path_generation(
    #     user_query="Discover research idea between bladder cancer and proteins.",
    # )


if __name__ == "__main__":
    """
    Usage example:
    python test/test_path_generation.py

    Notes:
    1. Ensure all dependency packages are installed
    2. Ensure knowledge graph and embeddings file paths are correct
    3. Ensure OpenAI API key is set
    """
    import os

    # Set API keys here
    os.environ['OPENAI_API_KEY'] = ''
    os.environ['SEMANTIC_SCHOLAR_API_KEY'] = ''

    # Check if API key is set
    if not os.environ.get('OPENAI_API_KEY'):
        print("[WARNING] OPENAI_API_KEY environment variable is not set")
        print("Please set the environment variable or uncomment the relevant lines in the script")

    asyncio.run(main())
