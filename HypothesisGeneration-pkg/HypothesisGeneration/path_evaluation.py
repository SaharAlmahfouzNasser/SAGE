"""
Path Evaluation Module

This module contains evaluation functions for generated paths, including:
- Logic: Compute the semantic similarity between a path and the user query
- Relevance: Compute the relevance of a path to the target domain
- Novelty: Compute the novelty of a path
- Surprise: Compute the surprise score of a path
"""

import numpy as np
from typing import List, Dict, Optional, Any
from scipy.spatial.distance import cosine
from scipy.stats import entropy
from sentence_transformers import SentenceTransformer
import re


# Global model instance (lazy loading)
_logic_model: Optional[SentenceTransformer] = None


def _get_logic_model(model_name: str = "all-MiniLM-L6-v2"):
    """
    Get or initialize the Logic evaluation model (Sentence-BERT)

    Args:
        model_name: Sentence-BERT model name, defaults to "all-MiniLM-L6-v2"

    Returns:
        SentenceTransformer model instance
    """
    global _logic_model
    if _logic_model is None:
        _logic_model = SentenceTransformer(model_name)
    return _logic_model


def evaluate_logic_score(
    user_query: str,
    path_string: str,
    model_name: str = "all-MiniLM-L6-v2",
    threshold: float = 0.5
) -> Dict[str, float]:
    """
    Evaluate the Logic score (semantic similarity) of a path

    Uses a Sentence-BERT model to compute the cosine similarity between the user query and the path.

    Args:
        user_query: User query string
        path_string: Path string (format: entity1 -- relation -- entity2 -- relation -- ...)
        model_name: Sentence-BERT model name, defaults to "all-MiniLM-L6-v2"
        threshold: Similarity threshold; paths below this value are considered "fake paths", defaults to 0.5

    Returns:
        A dictionary containing the following keys:
        - 'score': Logic score (cosine similarity, range 0-1)
        - 'passed': Boolean indicating whether the threshold check is passed
        - 'threshold': The threshold used
    """
    # Get the model
    model = _get_logic_model(model_name)

    # Convert the path to a natural language sentence
    # Path format: entity1 -- relation -- entity2 -- relation -- entity3
    # Converted to: entity1 relates to entity2 through relation, and entity2 relates to entity3 through relation
    path_sentence = _convert_path_to_sentence(path_string)

    # Encode the query and the path
    query_embedding = model.encode(user_query, convert_to_numpy=True)
    path_embedding = model.encode(path_sentence, convert_to_numpy=True)

    # Compute cosine similarity
    # The cosine function returns distance (0-2), which needs to be converted to similarity (0-1)
    cosine_distance = cosine(query_embedding, path_embedding)
    cosine_similarity = 1 - cosine_distance

    # Check whether the threshold is passed
    passed = cosine_similarity >= threshold

    return {
        'score': float(cosine_similarity),
        'passed': passed,
        'threshold': threshold
    }


def _convert_path_to_sentence(path_string: str) -> str:
    """
    Convert a path string to a natural language sentence

    Example:
    Input: "tertiary lymphoid structures||Phenotype -- promote -- immunotherapy||Process"
    Output: "tertiary lymphoid structures promotes immunotherapy"

    Args:
        path_string: Path string

    Returns:
        Natural language sentence
    """
    # Remove node type annotations (||Type)
    # For example: "entity||Type" -> "entity"
    import re
    path_string = re.sub(r'\|\|[A-Za-z]+', '', path_string)

    # Split the path into entities and relations
    parts = path_string.split(' -- ')

    if len(parts) < 2:
        # If the path is too short, return it directly
        return path_string

    # Build a natural language sentence
    # Format: entity1 [relation] entity2, and entity2 [relation] entity3, ...
    sentence_parts = []
    for i in range(0, len(parts) - 1, 2):
        if i + 1 < len(parts):
            entity1 = parts[i].strip()
            relation = parts[i + 1].strip()
            entity2 = parts[i + 2].strip() if i + 2 < len(parts) else ""

            # Build the relation phrase
            if entity2:
                sentence_parts.append(f"{entity1} {relation} {entity2}")
            else:
                sentence_parts.append(f"{entity1} {relation}")

    # If parsing fails, return the original path (cleaned)
    if not sentence_parts:
        return path_string.replace(' -- ', ' ').replace('||', ' ')

    # Join the parts with commas
    return ", ".join(sentence_parts)


def evaluate_paths_logic(
    user_query: str,
    paths: List[Dict],
    model_name: str = "all-MiniLM-L6-v2",
    threshold: float = 0.5,
) -> tuple[List[Dict], Dict[str, int]]:
    """
    Batch evaluate Logic scores for paths

    Args:
        user_query: User query string
        paths: List of paths, each path is a dictionary containing the 'path_string' key
        model_name: Sentence-BERT model name
        threshold: Similarity threshold

    Returns:
        tuple: (updated path list, statistics dictionary)
        - Path list: Each path dictionary has 'logic_score' and 'logic_passed' keys added
        - Statistics: Contains 'total', 'passed', 'filtered', etc.
    """
    evaluated_paths = []
    stats = {
        'total': len(paths),
        'passed': 0,
        'filtered': 0
    }

    for path in paths:
        path_string = path.get('path_string', '')
        if not path_string:
            continue

        # Evaluate Logic score
        logic_result = evaluate_logic_score(
            user_query=user_query,
            path_string=path_string,
            model_name=model_name,
            threshold=threshold
        )

        # Add evaluation results to the path dictionary
        path['logic_score'] = logic_result['score']
        path['logic_passed'] = logic_result['passed']
        path['logic_threshold'] = logic_result['threshold']

        # Statistics
        if logic_result['passed']:
            stats['passed'] += 1
        else:
            stats['filtered'] += 1

        evaluated_paths.append(path)

    return evaluated_paths, stats


def _extract_entities_from_path(path_string: str) -> List[str]:
    """
    Extract entity names from a path string

    Path format: entity1||Type -- relation -- entity2||Type -- relation -- ...

    Args:
        path_string: Path string

    Returns:
        List of entity names (with type annotations removed)
    """
    entities = []

    # Split the path
    parts = path_string.split(' -- ')

    # Extract entities (even-indexed positions are entities, odd-indexed positions are relations)
    for i in range(0, len(parts), 2):
        entity_with_type = parts[i].strip()
        # Remove type annotation ||Type
        entity = re.sub(r'\|\|[A-Za-z]+', '', entity_with_type).strip()
        if entity:
            entities.append(entity)

    return entities


def _extract_entity_types_from_path(path_string: str) -> List[str]:
    """
    Extract entity types from a path string

    Path format: entity1||Type -- relation -- entity2||Type -- relation -- ...

    Args:
        path_string: Path string

    Returns:
        List of entity types
    """
    entity_types = []

    # Split the path
    parts = path_string.split(' -- ')

    # Extract entity types (even-indexed positions are entities, odd-indexed positions are relations)
    for i in range(0, len(parts), 2):
        entity_with_type = parts[i].strip()
        # Extract the type annotation ||Type
        type_match = re.search(r'\|\|([A-Za-z]+)', entity_with_type)
        if type_match:
            entity_type = type_match.group(1)
            entity_types.append(entity_type)
        else:
            # If there is no type annotation, use "Unknown"
            entity_types.append("Unknown")

    return entity_types


def _get_entity_embedding(
    entity_name: str,
    node_embeddings: Dict[str, np.ndarray],
    embedding_tokenizer: Any = None,
    embedding_model: Any = None
) -> Optional[np.ndarray]:
    """
    Get the embedding vector of an entity

    First tries to look up in node_embeddings; if not found, uses embedding_model to encode

    Args:
        entity_name: Entity name
        node_embeddings: Node embeddings dictionary
        embedding_tokenizer: Tokenizer for the embedding model
        embedding_model: Embedding model

    Returns:
        Entity embedding vector, or None if it cannot be obtained
    """
    # Method 1: Look up directly in node_embeddings
    if entity_name in node_embeddings:
        embedding = node_embeddings[entity_name]
        # Ensure it is a numpy array and flatten
        if isinstance(embedding, np.ndarray):
            return embedding.flatten()
        else:
            return np.array(embedding).flatten()

    # Method 2: Try fuzzy matching (look for keys containing the entity name)
    for key in node_embeddings.keys():
        if entity_name.lower() in key.lower() or key.lower() in entity_name.lower():
            embedding = node_embeddings[key]
            if isinstance(embedding, np.ndarray):
                return embedding.flatten()
            else:
                return np.array(embedding).flatten()

    # Method 3: Use embedding_model to encode (if provided)
    if embedding_tokenizer is not None and embedding_model is not None:
        try:
            import torch
            inputs = embedding_tokenizer(entity_name, return_tensors="pt")
            with torch.no_grad():
                outputs = embedding_model(**inputs)
            embedding = outputs.last_hidden_state.mean(dim=1).detach().numpy().flatten()
            return embedding
        except Exception:
            pass

    return None


def evaluate_relevance_score(
    path_string: str,
    target_domain: str = "bladder cancer",
    node_embeddings: Dict[str, np.ndarray] = None,
    embedding_tokenizer: Any = None,
    embedding_model: Any = None
) -> Dict[str, float]:
    """
    Evaluate the Relevance score of a path

    Formula: Score_rel = 1 - (1/N) * Sigma ||e - c_target||_2

    Where:
    - e is the vector of each entity in the path
    - c_target is the domain center vector of the target domain
    - N is the number of entities in the path
    - ||.||_2 is the Euclidean distance (L2 norm)

    Args:
        path_string: Path string
        target_domain: Target domain (defaults to "bladder cancer")
        node_embeddings: Node embeddings dictionary
        embedding_tokenizer: Tokenizer for the embedding model (used to encode entities not in node_embeddings)
        embedding_model: Embedding model (used to encode entities not in node_embeddings)

    Returns:
        A dictionary containing the following keys:
        - 'score': Relevance score (range typically 0-1, but may be negative)
        - 'num_entities': Number of entities in the path
        - 'num_found': Number of entities for which embedding vectors were successfully found
    """
    if node_embeddings is None:
        return {
            'score': 0.0,
            'num_entities': 0,
            'num_found': 0,
            'error': 'node_embeddings not provided'
        }

    # Extract entities from the path
    entities = _extract_entities_from_path(path_string)

    if not entities:
        return {
            'score': 0.0,
            'num_entities': 0,
            'num_found': 0,
            'error': 'no entities found in path'
        }

    # Get the target domain center vector c_target
    c_target = _get_entity_embedding(
        target_domain,
        node_embeddings,
        embedding_tokenizer,
        embedding_model
    )

    if c_target is None:
        return {
            'score': 0.0,
            'num_entities': len(entities),
            'num_found': 0,
            'error': f'could not get embedding for target domain: {target_domain}'
        }

    # Normalize the target vector (L2 normalization)
    c_target_norm = c_target / (np.linalg.norm(c_target) + 1e-8)

    # Compute the Euclidean distance between each entity and c_target
    distances = []
    found_count = 0

    for entity in entities:
        entity_embedding = _get_entity_embedding(
            entity,
            node_embeddings,
            embedding_tokenizer,
            embedding_model
        )

        if entity_embedding is not None:
            # Ensure dimensions match
            if entity_embedding.shape != c_target_norm.shape:
                # Try to adjust dimensions
                min_dim = min(len(entity_embedding), len(c_target_norm))
                entity_embedding = entity_embedding[:min_dim]
                c_target_aligned = c_target_norm[:min_dim]
            else:
                c_target_aligned = c_target_norm

            # Normalize the entity embedding vector (L2 normalization)
            entity_norm = entity_embedding / (np.linalg.norm(entity_embedding) + 1e-8)

            # Compute the Euclidean distance after normalization
            # For normalized vectors, the distance range is typically [0, 2]
            distance = np.linalg.norm(entity_norm - c_target_aligned)
            distances.append(distance)
            found_count += 1

    if not distances:
        return {
            'score': 0.0,
            'num_entities': len(entities),
            'num_found': 0,
            'error': 'could not get embeddings for any entities in path'
        }

    # Compute average distance
    avg_distance = np.mean(distances)

    # Compute Relevance score: Score_rel = 1 - (1/N) * Sigma ||e - c_target||_2
    # After normalization, the distance range is typically [0, 2], so the score range is [-1, 1]
    # To get a score in the [0, 1] range, we can use: score = 1 - (avg_distance / 2)
    # Or use the original formula but accept negative scores
    # Here we use the normalized version to keep the score in the [0, 1] range
    relevance_score = 1.0 - (avg_distance / 2.0)

    # Ensure the score is in a reasonable range [0, 1]
    relevance_score = max(0.0, min(1.0, relevance_score))

    return {
        'score': float(relevance_score),
        'num_entities': len(entities),
        'num_found': found_count,
        'avg_distance': float(avg_distance)
    }


def evaluate_paths_relevance(
    paths: List[Dict],
    target_domain: str = "bladder cancer",
    node_embeddings: Dict[str, np.ndarray] = None,
    embedding_tokenizer: Any = None,
    embedding_model: Any = None
) -> List[Dict]:
    """
    Batch evaluate Relevance scores for paths

    Args:
        paths: List of paths, each path is a dictionary containing the 'path_string' key
        target_domain: Target domain (defaults to "bladder cancer")
        node_embeddings: Node embeddings dictionary
        embedding_tokenizer: Tokenizer for the embedding model
        embedding_model: Embedding model

    Returns:
        Updated path list, with 'relevance_score' and other keys added to each path dictionary
    """
    evaluated_paths = []

    for path in paths:
        path_string = path.get('path_string', '')
        if not path_string:
            continue

        # Evaluate Relevance score
        relevance_result = evaluate_relevance_score(
            path_string=path_string,
            target_domain=target_domain,
            node_embeddings=node_embeddings,
            embedding_tokenizer=embedding_tokenizer,
            embedding_model=embedding_model
        )

        # Add evaluation results to the path dictionary
        path['relevance_score'] = relevance_result['score']
        path['relevance_num_entities'] = relevance_result.get('num_entities', 0)
        path['relevance_num_found'] = relevance_result.get('num_found', 0)
        if 'error' in relevance_result:
            path['relevance_error'] = relevance_result['error']

        evaluated_paths.append(path)

    return evaluated_paths


def _get_entity_degree(
    entity_name: str,
    G: Any = None,
    node_embeddings: Dict[str, np.ndarray] = None
) -> Optional[int]:
    """
    Get the degree of an entity in the knowledge graph

    Args:
        entity_name: Entity name
        G: Knowledge graph (NetworkX graph object)
        node_embeddings: Node embeddings dictionary (used for looking up node names)

    Returns:
        The degree of the node, or None if not found
    """
    if G is None:
        return None

    # Method 1: Look up the node directly in the graph
    if entity_name in G:
        return G.degree(entity_name)

    # Method 2: Try fuzzy matching (look for nodes containing the entity name)
    for node in G.nodes():
        if entity_name.lower() in str(node).lower() or str(node).lower() in entity_name.lower():
            return G.degree(node)

    # Method 3: If node_embeddings is provided, try to find matching nodes in it
    if node_embeddings is not None:
        for key in node_embeddings.keys():
            if entity_name.lower() in key.lower() or key.lower() in entity_name.lower():
                if key in G:
                    return G.degree(key)

    return None


def evaluate_novelty_score(
    path_string: str,
    G: Any = None,
    node_embeddings: Dict[str, np.ndarray] = None
) -> Dict[str, float]:
    """
    Evaluate the Novelty score of a path

    Formula: Score_nov = (1/|P|) * Sigma_{e in P} -log(P(e))

    Where:
    - P(e) is the occurrence probability of the entity in the knowledge graph (degree-normalized value)
    - |P| is the number of entities in the path
    - The rarer the entity, the larger -log(P(e)), and the higher the novelty score

    How P(e) is computed:
    - P(e) = degree(e) / (2 * total_edges)
    - Or P(e) = degree(e) / sum(all_degrees)
    - Here we use P(e) = degree(e) / sum(all_degrees), so that the probabilities of all nodes sum to 1

    Args:
        path_string: Path string
        G: Knowledge graph (NetworkX graph object)
        node_embeddings: Node embeddings dictionary (used for looking up nodes)

    Returns:
        A dictionary containing the following keys:
        - 'score': Novelty score (higher means more novel)
        - 'num_entities': Number of entities in the path
        - 'num_found': Number of entities for which degrees were successfully found
        - 'avg_log_prob': Average -log(P(e)) value
    """
    if G is None:
        return {
            'score': 0.0,
            'num_entities': 0,
            'num_found': 0,
            'error': 'graph not provided'
        }

    # Extract entities from the path
    entities = _extract_entities_from_path(path_string)

    if not entities:
        return {
            'score': 0.0,
            'num_entities': 0,
            'num_found': 0,
            'error': 'no entities found in path'
        }

    # Compute the sum of degrees of all nodes (for normalization)
    total_degree_sum = sum(dict(G.degree()).values())

    if total_degree_sum == 0:
        return {
            'score': 0.0,
            'num_entities': len(entities),
            'num_found': 0,
            'error': 'graph has no edges'
        }

    # Compute -log(P(e)) for each entity
    log_probs = []
    found_count = 0

    for entity in entities:
        degree = _get_entity_degree(entity, G, node_embeddings)

        if degree is not None and degree >= 0:
            # Compute probability P(e) = degree(e) / sum(all_degrees)
            # Add a small epsilon to avoid log(0)
            prob = (degree + 1e-8) / (total_degree_sum + 1e-8)

            # Compute -log(P(e))
            # The smaller the probability (the rarer the entity), the larger -log(P(e))
            log_prob = -np.log(prob)
            log_probs.append(log_prob)
            found_count += 1

    if not log_probs:
        return {
            'score': 0.0,
            'num_entities': len(entities),
            'num_found': 0,
            'error': 'could not get degrees for any entities in path'
        }

    # Compute the average -log(P(e))
    avg_log_prob = np.mean(log_probs)

    # The Novelty score is the average -log(P(e))
    # Score_nov = (1/|P|) * Sigma_{e in P} -log(P(e)) = avg_log_prob
    novelty_score = avg_log_prob

    return {
        'score': float(novelty_score),
        'num_entities': len(entities),
        'num_found': found_count,
        'avg_log_prob': float(avg_log_prob),
        'total_degree_sum': int(total_degree_sum)
    }


def _compute_novelty_normalization_params(
    G: Any = None
) -> Dict[str, float]:
    """
    Compute normalization parameters for Novelty scores

    Compute -log(P(e)) values for all nodes in the graph and find the min and max values for normalization

    Args:
        G: Knowledge graph (NetworkX graph object)

    Returns:
        A dictionary containing 'min_log_prob' and 'max_log_prob'
    """
    if G is None:
        return {'min_log_prob': 0.0, 'max_log_prob': 1.0}

    # Compute the sum of degrees of all nodes
    total_degree_sum = sum(dict(G.degree()).values())

    if total_degree_sum == 0:
        return {'min_log_prob': 0.0, 'max_log_prob': 1.0}

    # Compute -log(P(e)) values for all nodes
    log_probs = []
    degrees = dict(G.degree())

    for node, degree in degrees.items():
        # Compute probability P(e) = degree(e) / sum(all_degrees)
        prob = (degree + 1e-8) / (total_degree_sum + 1e-8)
        # Compute -log(P(e))
        log_prob = -np.log(prob)
        log_probs.append(log_prob)

    if not log_probs:
        return {'min_log_prob': 0.0, 'max_log_prob': 1.0}

    min_log_prob = np.min(log_probs)
    max_log_prob = np.max(log_probs)

    # If max and min are equal, avoid division by zero
    if max_log_prob == min_log_prob:
        return {'min_log_prob': 0.0, 'max_log_prob': 1.0}

    return {
        'min_log_prob': float(min_log_prob),
        'max_log_prob': float(max_log_prob)
    }


def evaluate_paths_novelty(
    paths: List[Dict],
    G: Any = None,
    node_embeddings: Dict[str, np.ndarray] = None
) -> List[Dict]:
    """
    Batch evaluate Novelty scores for paths (with normalization)

    Args:
        paths: List of paths, each path is a dictionary containing the 'path_string' key
        G: Knowledge graph (NetworkX graph object)
        node_embeddings: Node embeddings dictionary

    Returns:
        Updated path list, with 'novelty_score' and other keys added to each path dictionary
        Novelty scores are normalized to the [0, 1] range
    """
    evaluated_paths = []

    # Compute normalization parameters (based on the entire graph)
    norm_params = _compute_novelty_normalization_params(G)
    min_log_prob = norm_params['min_log_prob']
    max_log_prob = norm_params['max_log_prob']
    log_prob_range = max_log_prob - min_log_prob

    for path in paths:
        path_string = path.get('path_string', '')
        if not path_string:
            continue

        # Evaluate Novelty score
        novelty_result = evaluate_novelty_score(
            path_string=path_string,
            G=G,
            node_embeddings=node_embeddings
        )

        # Normalize the score to the [0, 1] range
        raw_score = novelty_result.get('score', 0.0)
        if log_prob_range > 0:
            # Min-max normalization: (value - min) / (max - min)
            normalized_score = (raw_score - min_log_prob) / log_prob_range
            # Ensure it is within the [0, 1] range
            normalized_score = max(0.0, min(1.0, normalized_score))
        else:
            normalized_score = 0.0

        # Add evaluation results to the path dictionary
        path['novelty_score'] = normalized_score
        path['novelty_raw_score'] = raw_score  # Keep the raw score for reference
        path['novelty_num_entities'] = novelty_result.get('num_entities', 0)
        path['novelty_num_found'] = novelty_result.get('num_found', 0)
        if 'error' in novelty_result:
            path['novelty_error'] = novelty_result['error']

        evaluated_paths.append(path)

    return evaluated_paths


def _compute_category_distribution(categories: List[str]) -> Dict[str, float]:
    """
    Compute the category distribution (probability distribution)

    Args:
        categories: List of categories

    Returns:
        A dictionary mapping categories to probabilities
    """
    if not categories:
        return {}

    # Count the occurrences of each category
    category_counts = {}
    for cat in categories:
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Convert to a probability distribution
    total = len(categories)
    distribution = {cat: count / total for cat, count in category_counts.items()}

    return distribution


def _kl_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
    """
    Compute KL divergence KL(P||Q)

    KL(P||Q) = Sigma P(x) * log(P(x) / Q(x))

    Args:
        p: First probability distribution
        q: Second probability distribution

    Returns:
        KL divergence value
    """
    # Get all possible categories
    all_categories = set(p.keys()) | set(q.keys())

    if not all_categories:
        return 0.0

    kl_sum = 0.0
    for cat in all_categories:
        p_val = p.get(cat, 0.0)
        q_val = q.get(cat, 0.0)

        # Avoid log(0) by adding a small epsilon
        if p_val > 0:
            if q_val > 0:
                kl_sum += p_val * np.log(p_val / q_val)
            else:
                # If q_val is 0, KL divergence is infinite; here we use a large value
                kl_sum += p_val * np.log(p_val / 1e-10)

    return kl_sum


def _compute_expected_category_distribution(
    G: Any = None,
    source_entity: Optional[str] = None,
    target_entity: Optional[str] = None
) -> Dict[str, float]:
    """
    Compute the expected category distribution for the "conventional domain" (baseline distribution Q)

    This represents the probability that each category should appear under conventional pharmacological/biological expectations.

    Method:
    1. If source and target are provided, try to learn the category distribution from common paths from source to target in the graph
    2. Otherwise, use the global category distribution of the graph as the baseline

    Args:
        G: Knowledge graph (NetworkX graph object)
        source_entity: Source entity (optional)
        target_entity: Target entity (optional)

    Returns:
        Expected category distribution dictionary {category: probability}
    """
    if G is None or not G.nodes():
        # If there is no graph, return a uniform distribution (all categories with equal probability)
        return {}

    # Compute the category distribution of all nodes in the graph as the baseline
    all_node_types = []
    for node in G.nodes():
        node_type = G.nodes[node].get('type', 'Unknown')
        if node_type and node_type != 'Unknown':
            all_node_types.append(node_type)

    if not all_node_types:
        return {}

    # Compute the global category distribution
    return _compute_category_distribution(all_node_types)


def evaluate_surprise_score(
    path_string: str,
    G: Any = None,
    expected_distribution: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """
    Evaluate the Surprise score (degree of surprise / cross-domain degree) of a path

    Core idea: Measure the difference between the actual category distribution of a path
    and the expected category distribution of the "conventional domain".

    Mathematical definition:
    Surprise = KL(P_actual || P_expected)

    Where:
    - P_actual: The actual category distribution of the path
    - P_expected: The expected category distribution of the conventional domain (baseline distribution)

    Intuitive explanation:
    - If the path contains "unexpected" categories (low probability in conventional expectations but actually present), the KL divergence is high
    - For example: Jumping directly from Drug to gut microbiome (Microbiome) -- this cross-domain jump produces high surprise
    - If the path stays within the same circle (e.g., Gene -> Gene -> Gene), the surprise is low

    Examples:
    Path 1 (low surprise): Metformin(Drug) -> AMPK(Protein) -> blood glucose(Process) -> diabetes(Disease)
    Explanation: From drug to protein to disease -- this is a textbook standard workflow with smooth category flow and low KL divergence.

    Path 2 (high surprise): Metformin(Drug) -> gut microbiome(Microbiome) -> immune response(Process) -> diabetes(Disease)
    Explanation: The sudden insertion of "gut microbiome", which has low probability in traditional pharmacological expectations but actually appears, causes distribution oscillation and high KL divergence.

    Args:
        path_string: Path string
        G: Knowledge graph (used to compute the expected distribution)
        expected_distribution: Expected category distribution (if provided, used directly; otherwise computed from G)

    Returns:
        A dictionary containing the following keys:
        - 'score': Surprise score (KL divergence, raw value, not normalized)
        - 'num_entities': Number of entities in the path
        - 'num_categories': Number of distinct categories in the path
        - 'kl_divergence': KL divergence value
        - 'expected_distribution': Expected distribution (for debugging)
        - 'actual_distribution': Actual distribution (for debugging)
    """
    # Extract the actual categories of the path
    entity_types = _extract_entity_types_from_path(path_string)

    if len(entity_types) < 1:
        return {
            'score': 0.0,
            'num_entities': 0,
            'num_categories': 0,
            'kl_divergence': 0.0,
            'error': 'no entities in path'
        }

    # Compute the actual category distribution P_actual of the path
    actual_distribution = _compute_category_distribution(entity_types)

    # Get the expected category distribution P_expected
    if expected_distribution is None:
        expected_distribution = _compute_expected_category_distribution(G=G)

    # If the expected distribution is empty, use a uniform distribution as fallback
    if not expected_distribution:
        # Get all possible categories
        all_categories = set(entity_types)
        if all_categories:
            # Uniform distribution: equal probability for each category
            uniform_prob = 1.0 / len(all_categories)
            expected_distribution = {cat: uniform_prob for cat in all_categories}
        else:
            return {
                'score': 0.0,
                'num_entities': len(entity_types),
                'num_categories': len(set(entity_types)),
                'kl_divergence': 0.0,
                'error': 'cannot compute expected distribution'
            }

    # Compute KL divergence: KL(P_actual || P_expected)
    # This measures the difference between the actual distribution and the expected distribution
    kl_divergence = _kl_divergence(actual_distribution, expected_distribution)

    # The Surprise score is the KL divergence
    surprise_score = kl_divergence

    return {
        'score': float(surprise_score),
        'num_entities': len(entity_types),
        'num_categories': len(set(entity_types)),
        'kl_divergence': float(kl_divergence),
        'expected_distribution': expected_distribution.copy(),
        'actual_distribution': actual_distribution.copy()
    }


def _compute_surprise_normalization_params(
    paths: List[Dict],
    G: Any = None,
    expected_distribution: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """
    Compute normalization parameters for Surprise scores

    Compute Surprise scores for all paths and find the min and max values for normalization

    Args:
        paths: List of paths
        G: Knowledge graph (used to compute the expected distribution)
        expected_distribution: Expected category distribution (if provided, used directly)

    Returns:
        A dictionary containing 'min_surprise' and 'max_surprise'
    """
    if not paths:
        return {'min_surprise': 0.0, 'max_surprise': 1.0}

    # If the expected distribution is not provided, compute it once from the graph (shared by all paths)
    if expected_distribution is None:
        expected_distribution = _compute_expected_category_distribution(G=G)

    surprise_scores = []

    for path in paths:
        path_string = path.get('path_string', '')
        if not path_string:
            continue

        surprise_result = evaluate_surprise_score(
            path_string=path_string,
            G=G,
            expected_distribution=expected_distribution
        )
        if 'error' not in surprise_result:
            surprise_scores.append(surprise_result['score'])

    if not surprise_scores:
        return {'min_surprise': 0.0, 'max_surprise': 1.0}

    min_surprise = np.min(surprise_scores)
    max_surprise = np.max(surprise_scores)

    # If max and min are equal, avoid division by zero
    if max_surprise == min_surprise:
        return {'min_surprise': 0.0, 'max_surprise': 1.0}

    return {
        'min_surprise': float(min_surprise),
        'max_surprise': float(max_surprise)
    }


def evaluate_paths_surprise(
    paths: List[Dict],
    G: Any = None
) -> List[Dict]:
    """
    Batch evaluate Surprise scores for paths (with normalization)

    Args:
        paths: List of paths, each path is a dictionary containing the 'path_string' key
        G: Knowledge graph (used to compute the expected distribution)

    Returns:
        Updated path list, with 'surprise_score' and other keys added to each path dictionary
        Surprise scores are normalized to the [0, 1] range
    """
    evaluated_paths = []

    # Compute the expected distribution (shared by all paths)
    expected_distribution = _compute_expected_category_distribution(G=G)

    # Compute normalization parameters (based on all paths)
    norm_params = _compute_surprise_normalization_params(
        paths=paths,
        G=G,
        expected_distribution=expected_distribution
    )
    min_surprise = norm_params['min_surprise']
    max_surprise = norm_params['max_surprise']
    surprise_range = max_surprise - min_surprise

    for path in paths:
        path_string = path.get('path_string', '')
        if not path_string:
            continue

        # Evaluate Surprise score
        surprise_result = evaluate_surprise_score(
            path_string=path_string,
            G=G,
            expected_distribution=expected_distribution
        )

        # Normalize the score to the [0, 1] range
        raw_score = surprise_result.get('score', 0.0)
        if surprise_range > 0:
            # Min-max normalization: (value - min) / (max - min)
            normalized_score = (raw_score - min_surprise) / surprise_range
            # Ensure it is within the [0, 1] range
            normalized_score = max(0.0, min(1.0, normalized_score))
        else:
            normalized_score = 0.0

        # Add evaluation results to the path dictionary
        path['surprise_score'] = normalized_score
        path['surprise_raw_score'] = raw_score  # Keep the raw score for reference
        path['surprise_num_entities'] = surprise_result.get('num_entities', 0)
        path['surprise_num_categories'] = surprise_result.get('num_categories', 0)
        path['surprise_kl_divergence'] = surprise_result.get('kl_divergence', 0.0)
        if 'error' in surprise_result:
            path['surprise_error'] = surprise_result['error']

        evaluated_paths.append(path)

    return evaluated_paths


def compute_weighted_total_score(
    paths: List[Dict],
    weights: Dict[str, float] = None,
    evaluate_logic: bool = True,
    evaluate_relevance: bool = True,
    evaluate_novelty: bool = True,
    evaluate_surprise: bool = True
) -> List[Dict]:
    """
    Compute the weighted total score for paths

    Formula:
    Total Score = w_logic * Logic + w_relevance * Relevance + w_novelty * Novelty + w_surprise * Surprise

    Default weight values:
    - Logic: 0.2
    - Relevance: 0.2
    - Novelty: 0.3
    - Surprise: 0.3

    Args:
        paths: List of paths; each path dictionary should contain the corresponding evaluation score keys:
            - 'logic_score' (if evaluate_logic=True)
            - 'relevance_score' (if evaluate_relevance=True)
            - 'novelty_score' (if evaluate_novelty=True)
            - 'surprise_score' (if evaluate_surprise=True)
        weights: Weight dictionary in the format {'logic': 0.2, 'relevance': 0.2, 'novelty': 0.3, 'surprise': 0.3}
                If None, default weights are used
        evaluate_logic: Whether to include the Logic score
        evaluate_relevance: Whether to include the Relevance score
        evaluate_novelty: Whether to include the Novelty score
        evaluate_surprise: Whether to include the Surprise score

    Returns:
        Updated path list, with the following keys added to each path dictionary:
        - 'weighted_total_score': Weighted total score
        - 'score_components': Dictionary of contributions from each metric
        - 'score_weights': Dictionary of weights used
    """
    # Default weight configuration: encourages innovation
    if weights is None:
        weights = {
            'logic': 0.2,
            'relevance': 0.2,
            'novelty': 0.3,
            'surprise': 0.3
        }

    # Verify that the weights sum to 1.0 (allowing a small tolerance)
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 1e-6:
        import warnings
        warnings.warn(
            f"Weights sum to {weight_sum:.6f}, not 1.0. Normalizing weights.",
            UserWarning
        )
        # Normalize weights
        weights = {k: v / weight_sum for k, v in weights.items()}

    evaluated_paths = []

    for path in paths:
        total_score = 0.0
        components = {}

        # Logic score
        if evaluate_logic and 'logic_score' in path:
            logic_score = path['logic_score']
            weight = weights.get('logic', 0.0)
            components['logic'] = logic_score * weight
            total_score += components['logic']

        # Relevance score
        if evaluate_relevance and 'relevance_score' in path:
            relevance_score = path['relevance_score']
            weight = weights.get('relevance', 0.0)
            components['relevance'] = relevance_score * weight
            total_score += components['relevance']

        # Novelty score
        if evaluate_novelty and 'novelty_score' in path:
            novelty_score = path['novelty_score']
            weight = weights.get('novelty', 0.0)
            components['novelty'] = novelty_score * weight
            total_score += components['novelty']

        # Surprise score
        if evaluate_surprise and 'surprise_score' in path:
            surprise_score = path['surprise_score']
            weight = weights.get('surprise', 0.0)
            components['surprise'] = surprise_score * weight
            total_score += components['surprise']

        # Store the weighted total score and the contributions of each component
        path['weighted_total_score'] = total_score
        path['score_components'] = components
        path['score_weights'] = weights.copy()

        evaluated_paths.append(path)

    return evaluated_paths


def sort_paths_by_score(
    paths: List[Dict],
    score_key: str = 'weighted_total_score',
    reverse: bool = True,
    renumber: bool = True
) -> List[Dict]:
    """
    Sort paths by a specified score and optionally renumber them

    By default, sorts by weighted total score (weighted_total_score) from high to low and renumbers.
    The highest-scoring path is numbered 1, incrementing sequentially.

    Args:
        paths: List of paths; each path dictionary should contain the score key
        score_key: Name of the score key used for sorting, defaults to 'weighted_total_score'
        reverse: Whether to sort in descending order (True for high to low, False for low to high), defaults to True
        renumber: Whether to renumber, defaults to True. If True, the highest-scoring path is numbered 1, incrementing sequentially

    Returns:
        Sorted path list; if renumber=True, the 'number' field of each path will be renumbered
    """
    # Create a copy of the paths to avoid modifying the original list
    sorted_paths = paths.copy()

    # Sort by the specified score
    # If a path does not have the score key, set it to 0 (placed at the end)
    sorted_paths.sort(
        key=lambda x: x.get(score_key, 0.0),
        reverse=reverse
    )

    # Renumber: highest-scoring path is numbered 1, incrementing sequentially
    if renumber:
        for idx, path in enumerate(sorted_paths, start=1):
            path['number'] = idx

    return sorted_paths
