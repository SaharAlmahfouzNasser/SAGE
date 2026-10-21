# # Modified
# # ==============================
# # Utility and System Libraries
# # ==============================
# import functools
# import os
# import random
# import time
# from typing import Annotated, Optional, Union
# import requests
# import time
# from xml.etree import ElementTree as ET
# import os
# from typing import Optional, Dict, List
# from requests.adapters import HTTPAdapter
# from urllib3.util.retry import Retry
# import requests

# try:
#     get_ipython()
# except NameError:
#     pass

# import seaborn as sns  # For more attractive plotting
# sns.set_palette("hls")

# from GraphReasoning import *

# SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# NCBI_API_KEY = os.getenv("NCBI_API_KEY")

# _ALLOWED_TYPES = {
#     # ==================== Molecular Biology ====================
#     "Gene", "Protein", "Enzyme", "Peptide", "AminoAcid",
#     "DNA", "RNA", "LncRNA", "RNAi", "Transcript",
#     "Nucleic", "NucleicAcid", "Molecule", "Antibody", "Antigen",
    
#     # ==================== Cellular ====================
#     "Cell", "CellType", "CellLine", "CellCluster", "StemCell",
#     "Neuron", "Exosome", "Nucleus",
    
#     # ==================== Anatomical ====================
#     "Organ", "Tissue", "AnatomicalStructure", "Anatomy",
#     "Brain", "Bone", "Muscle", "Nerve", "Artery", "Vein",
#     "Vessel", "Lymph", "LymphNode", "Gland", "Duct",
#     "Urethra", "Vertebra", "SpinalSegment",
    
#     # ==================== Disease & Pathology ====================
#     "Disease", "Cancer", "CancerType", "Tumor", "Syndrome",
#     "Symptom", "Condition", "Complication", "Injury", "Anomaly",
#     "Metastasis", "Loss",
    
#     # ==================== Pharmacology ====================
#     "Drug", "Compound", "Chemical", "Treatment", "Therapy",
#     "Vaccine", "Nanoparticle", "Ion",
    
#     # ==================== Biochemistry ====================
#     "Pathway", "Process", "Metabolite", "Cytokine",
#     "Hormone", "Neurotransmitter", "Biomarker", "Marker",
#     "Receptor", "Signal",
    
#     # ==================== Genetics ====================
#     "Genetic", "Genome", "Genomic", "Genotype", "Variant",
#     "Mutation", "Chromosome", "Chromosomal", "Karyotype",
#     "Plasmid", "Vector",
    
#     # ==================== Clinical ====================
#     "Patient", "PatientGroup", "Cohort", "ClinicalTrial",
#     "ClinicalFactor", "ClinicalVariable", "Outcome",
#     "MedicalProcedure", "Surgery", "SurgicalProcedure",
#     "MedicalProfessional", "Surgeon", "Specialist",
    
#     # ==================== Research & Methods ====================
#     "Method", "Technique", "Assay", "Protocol", "Procedure",
#     "Measurement", "Test", "Experiment", "Study",
#     "ImagingTechnique", "Imaging",
    
#     # ==================== Data & Informatics ====================
#     "Data", "Dataset", "Database", "Model", "Algorithm",
#     "Network", "Graph", "Classification", "Ontology",
#     "Reference", "Publication", "Atlas",
    
#     # ==================== Organisms ====================
#     "Organism", "Species", "Genus", "Phylum",
#     "Bacteria", "Bacterium", "Virus", "Parasite",
#     "Microorganism", "Microbiota", "Microbiome",
#     "Mouse", "Embryo", "Plant", "Cultivar",
    
#     # ==================== Demographics & Social ====================
#     "Age", "Gender", "Sex", "Race", "Ethnicity",
#     "Socioeconomic", "SocioeconomicStatus", "Population",
#     "Demographic",
    
#     # ==================== Medical Factors ====================
#     "RiskFactor", "Risk", "RiskScore", "RiskCategory",
#     "Phenotype", "Trait", "Characteristic", "Feature",
#     "Grade", "Stage", "Subtype", "Type",
    
#     # ==================== Equipment & Materials ====================
#     "Device", "Catheter", "Stent", "Sling", "Robot",
#     "Laser", "Hardware", "Software", "Tool",
#     "Material", "Matrix", "Particle",
    
#     # ==================== Location & Organization ====================
#     "Location", "Region", "Space", "Layer", "Section",
#     "Hospital", "Facility", "Laboratory", "Organization",
    
#     # ==================== Other ====================
#     "Diet", "Food", "Fluid", "Channel", "Fiber",
#     "Bundle", "Complex", "Component", "Target",
#     "Function", "Effect", "Phenomenon", "Evidence",
#     "Involvement", "Representation", "Modification",
#     "Prevalence", "Cost", "Service", "Insurance",
#     "Questionnaire", "Criteria", "Definition",
#     "Information", "Image", "Specimen", "Sample",
#     "Case", "Field", "Domain", "Task", "Object",
#     "Factor", "Parameter", "Metric", "Result",
#     "Percentage", "Size", "Temperature", "Dosage",
#     "TimePoint", "TimePeriod", "Setting", "Modality",
# }
# _ALLOWED_DISEASE_SUBSTR = ("bladder cancer", "blca", "tcga-blca")


# def _node_type(nid: str) -> str:
#     """Extract the type part from a 'name||Type' formatted node id."""
#     return str(nid).split("||", 1)[1] if "||" in str(nid) else ""

# def _node_name(nid: str) -> str:
#     """Extract the name part from a 'name||Type' formatted node id."""
#     return str(nid).split("||", 1)[0]

# def _keep_node(nid: str) -> bool:
#     """Filter nodes: keep only allowed types and disease substrings."""
#     t = _node_type(nid)
#     if t not in _ALLOWED_TYPES:
#         return False
#     if t == "Disease":
#         nm = _node_name(nid).lower()
#         return any(k in nm for k in _ALLOWED_DISEASE_SUBSTR)
#     return True

# def clean_path_string(path_str: str) -> str:
#     """
#     Clean noisy path strings of the form:
#     'A||Type -- rel -- B||Type -- ...'

#     - Keep only nodes that pass _keep_node
#     - Preserve relations only between valid nodes
#     - Deduplicate consecutive nodes
#     - If everything is removed, return original string
#     """
#     if not path_str:
#         return path_str

#     toks = [t.strip() for t in path_str.split("--")]
#     if not toks:
#         return path_str

#     cleaned = []
#     i = 0
#     # Pattern: Node -- rel -- Node -- rel -- Node ...
#     while i < len(toks):
#         token = toks[i]
#         if "||" in token:  # Node
#             if _keep_node(token):
#                 if not cleaned or cleaned[-1] != token:
#                     cleaned.append(token)
#                 if i + 2 < len(toks):
#                     rel = toks[i + 1]
#                     nxt = toks[i + 2]
#                     if "||" in nxt and _keep_node(nxt):
#                         cleaned.append(rel)
#                         cleaned.append(nxt)
#                         i += 2
#         i += 1

#     # Deduplicate consecutive nodes
#     dedup = []
#     for t in cleaned:
#         if not dedup or dedup[-1] != t:
#             dedup.append(t)

#     # Fallback if nothing valid left
#     if len(dedup) < 3:
#         return path_str

#     return " -- ".join(dedup)

# def create_path(
#     G,
#     embedding_tokenizer,
#     embedding_model,
#     node_embeddings,
#     generate_graph_expansion=None,
#     second_hop=False,
#     data_dir="./",
#     save_files=False,
#     verbatim=False,
#     keyword_1=None,
#     keyword_2=None,
#     shortest_path=True,
#     top_k=5,
#     randomness_factor=0,
#     num_random_waypoints=0,
# ):
#     """Create a path between two keywords, optionally shortest-path or randomized."""
#     if keyword_1 is None or keyword_2 is None:
#         random_nodes = random.sample(list(G.nodes()), 2)
#         keyword_1 = keyword_1 or random_nodes[0]
#         keyword_2 = keyword_2 or random_nodes[1]

#     if shortest_path:
#         (
#             (best_node_1, best_similarity_1, best_node_2, best_similarity_2),
#             path,
#             path_graph,
#             shortest_path_length,
#             fname,
#             graph_GraphML,
#         ) = find_path(
#             G,
#             node_embeddings,
#             embedding_tokenizer,
#             embedding_model,
#             second_hop=False,
#             data_dir=data_dir,
#             save_files=False,
#             keyword_1=keyword_1,
#             keyword_2=keyword_2,
#         )

#     else:  # Randomized path
#         if randomness_factor > 0 or num_random_waypoints > 0:
#             path, path_graph, shortest_path_length, _, _ = (
#                 heuristic_path_with_embeddings_with_randomization_waypoints(
#                     G,
#                     embedding_tokenizer,
#                     embedding_model,
#                     keyword_1,
#                     keyword_2,
#                     node_embeddings,
#                     top_k=5,
#                     second_hop=False,
#                     data_dir=data_dir,
#                     verbatim=verbatim,
#                     save_files=False,
#                     randomness_factor=randomness_factor,
#                     num_random_waypoints=num_random_waypoints,
#                 )
#             )
#         else:
#             path, path_graph, shortest_path_length, _, _ = heuristic_path_with_embeddings(
#                 G,
#                 embedding_tokenizer,
#                 embedding_model,
#                 keyword_1,
#                 keyword_2,
#                 node_embeddings,
#                 top_k=top_k,
#                 second_hop=False,
#                 data_dir=data_dir,
#                 verbatim=verbatim,
#                 save_files=save_files,
#             )

#     path_list_for_vis, path_list_for_vis_string = path_list = print_path_with_edges_as_list(
#         G, path, keywords_separator=" -- "
#     )

#     return path_list_for_vis, path_list_for_vis_string

# def create_multi_keyword_path(keywords, G, embedding_tokenizer, embedding_model, node_embeddings):
#     """Create continuous paths through multiple keywords."""
#     all_paths = []
#     all_path_strings = []
#     combined_path_list = []

#     for i in range(len(keywords) - 1):
#         keyword_1 = keywords[i]
#         keyword_2 = keywords[i + 1]

#         try:
#             path_list_for_vis, path_list_for_vis_string = create_path(
#                 G,
#                 embedding_tokenizer,
#                 embedding_model,
#                 node_embeddings,
#                 generate_graph_expansion=None,
#                 randomness_factor=0.2,
#                 num_random_waypoints=4,
#                 shortest_path=True,
#                 second_hop=False,
#                 data_dir="./",
#                 save_files=False,
#                 verbatim=False,
#                 keyword_1=keyword_1,
#                 keyword_2=keyword_2,
#             )

#             all_paths.append(path_list_for_vis)
#             all_path_strings.append(path_list_for_vis_string)

#             if i == 0:
#                 combined_path_list.extend(path_list_for_vis)
#             else:
#                 if len(path_list_for_vis) > 1:
#                     combined_path_list.extend(path_list_for_vis[1:])
#                 else:
#                     combined_path_list.extend(path_list_for_vis)

#         except Exception as e:
#             print(f"Path {i + 1} ({keyword_1} → {keyword_2}) failed: {str(e)}")
#             if i == 0:
#                 combined_path_list.extend([keyword_1, "", keyword_2])
#             else:
#                 combined_path_list.extend(["", keyword_2])

#     combined_path_string = " -- ".join(str(item) for item in combined_path_list if item != "")
#     individual_segments = "\n\n".join(f"Segment {i + 1}: {path_str}" for i, path_str in enumerate(all_path_strings))

#     return combined_path_list, combined_path_string

# def generate_path(
#     G,
#     embedding_tokenizer,
#     embedding_model,
#     node_embeddings,
#     keyword_1: Annotated[str, "First keyword"],
#     keyword_2: Annotated[str, "Second keyword"],
#     keyword_3: Annotated[Union[str, None], "Third keyword (optional)"] = None,
#     keyword_4: Annotated[Union[str, None], "Fourth keyword (optional)"] = None,
#     keyword_5: Annotated[Union[str, None], "Fifth keyword (optional)"] = None,
# ) -> str:
#     """Generate a path string between 2–5 keywords using helper functions."""
#     keyword_list = [keyword_1, keyword_2]
#     for kw in [keyword_3, keyword_4, keyword_5]:
#         if kw is not None and str(kw).strip():
#             keyword_list.append(str(kw).strip())

#     if len(keyword_list) == 2:
#         path_list_for_vis, path_list_for_vis_string = create_path(
#             G,
#             embedding_tokenizer,
#             embedding_model,
#             node_embeddings,
#             generate_graph_expansion=None,
#             randomness_factor=0.2,
#             num_random_waypoints=4,
#             shortest_path=True,
#             second_hop=False,
#             data_dir="./",
#             save_files=False,
#             verbatim=False,
#             keyword_1=keyword_list[0],
#             keyword_2=keyword_list[1],
#         )
#         return path_list_for_vis_string
#     else:
#         combined_path_list, combined_path_string = create_multi_keyword_path(
#             keyword_list, G, embedding_tokenizer, embedding_model, node_embeddings
#         )
#         return combined_path_string

# import time
# import requests
# from requests.adapters import HTTPAdapter
# from urllib3.util.retry import Retry

# # ==================== Enhanced Semantic Scholar Search (Recommended) ====================
# def semanticscholar_search(query: str, max_results: int = 10) -> str:
#     """
#     Search Semantic Scholar academic database (includes preprints)

#     Args:
#         query: Search query
#         max_results: Maximum number of results

#     Returns:
#         Formatted search results
#     """
#     try:
#         url = "https://api.semanticscholar.org/graph/v1/paper/search"
        
#         # ✅ Get API key from environment variable
#         api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")

#         # ✅ Set request headers
#         headers = {}
#         if api_key:
#             headers["x-api-key"] = api_key
#             print(f"🔑 Using Semantic Scholar API key (ends with: ...{api_key[-4:]})")
#         else:
#             print("⚠️ No Semantic Scholar API key found, using rate-limited access")
        
#         params = {
#             "query": query,
#             "limit": max_results,
#             "fields": "title,abstract,year,authors,venue,externalIds,url,publicationTypes"
#         }
        
#         # ✅ Shorter delay when API key is available
#         if api_key:
#             time.sleep(0.5)  # More lenient rate limit with key
#         else:
#             time.sleep(1.5)  # More conservative without key

#         # ✅ Add headers parameter
#         response = requests.get(url, params=params, headers=headers, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         papers = data.get("data", [])
        
#         if not papers:
#             return f"No results found in Semantic Scholar for query: {query}"
        
#         results = []
#         for paper in papers:
#             title = paper.get("title", "No title")
#             abstract = paper.get("abstract", "No abstract available")
#             year = paper.get("year", "Unknown")
#             venue = paper.get("venue", "Unknown venue")
#             url_link = paper.get("url", "")
            
#             # Detect if it is a preprint
#             pub_types = paper.get("publicationTypes", [])
#             is_preprint = "Preprint" in pub_types if pub_types else False
            
#             # Detect source
#             venue_lower = venue.lower() if venue else ""
#             if "biorxiv" in venue_lower:
#                 source = "bioRxiv (preprint)"
#             elif "medrxiv" in venue_lower:
#                 source = "medRxiv (preprint)"
#             elif "arxiv" in venue_lower:
#                 source = "arXiv (preprint)"
#             elif is_preprint:
#                 source = f"{venue} (preprint)"
#             else:
#                 source = venue
            
#             # Get DOI
#             external_ids = paper.get("externalIds", {})
#             doi = external_ids.get("DOI", "")
            
#             # Get authors
#             authors = paper.get("authors", [])
#             author_names = [a.get("name", "") for a in authors[:3]]
#             author_str = ", ".join(author_names)
#             if len(authors) > 3:
#                 author_str += " et al."
            
#             results.append({
#                 "title": title,
#                 "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
#                 "year": str(year),
#                 "source": source,
#                 "authors": author_str,
#                 "url": url_link,
#                 "doi": doi,
#                 "is_preprint": is_preprint
#             })
        
#         # Format output
#         output = f"Semantic Scholar Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Authors: {paper['authors']}\n"
#             output += f"   Source: {paper['source']}\n"
#             output += f"   Year: {paper['year']}\n"
#             if paper['doi']:
#                 output += f"   DOI: {paper['doi']}\n"
#             output += f"   URL: {paper['url']}\n"
#             if paper['abstract'] != "No abstract available":
#                 output += f"   Abstract: {paper['abstract']}\n"
#             output += "\n"
        
#         return output
        
#     except requests.exceptions.HTTPError as e:
#         if e.response.status_code == 429:
#             return f"Rate limit exceeded for Semantic Scholar. Please try again later."
#         return f"Error searching Semantic Scholar: {str(e)}"
#     except Exception as e:
#         return f"Error searching Semantic Scholar: {str(e)}"
        
# def instrument(tool_name: str, fn, tool_events: list):
#     """Wrap a tool function to log execution time, args, and results into tool_events."""
#     @functools.wraps(fn)
#     def wrapped(*args, **kwargs):
#         t0 = time.time()
#         try:
#             result = fn(*args, **kwargs)
#             elapsed = int((time.time() - t0) * 1000)
#             preview = (str(result)[:500] + "…") if isinstance(result, str) and len(result) > 500 else str(result)
#             print(f"[TOOL] {tool_name} called in {elapsed} ms")
#             tool_events.append(
#                 {"tool": tool_name, "elapsed_ms": elapsed, "args": {"args": list(args), "kwargs": kwargs}, "result_preview": preview}
#             )
#             return result
#         except Exception as e:
#             elapsed = int((time.time() - t0) * 1000)
#             print(f"[TOOL] {tool_name} FAILED in {elapsed} ms: {e}")
#             tool_events.append(
#                 {"tool": tool_name, "elapsed_ms": elapsed, "args": {"args": list(args), "kwargs": kwargs}, "error": repr(e)}
#             )
#             raise
#     return wrapped

# import re

# def extract_keywords(sentence: str, max_k: int = 3) -> list[str]:
#     """
#     Minimal implementation: extract keyword candidates from a sentence.
#     - Split by non-alphanumeric
#     - Filter stop words
#     - Deduplicate
#     - Sort by length
#     """
#     toks = re.findall(r"[A-Za-z0-9+\-_/]+", sentence.lower())
#     stop = {"the","a","an","and","or","of","to","in","on","for","with","between","from","into","as","by"}
#     cand = [t for t in toks if t not in stop and len(t) > 2]

#     uniq, seen = [], set()
#     for t in cand:
#         if t not in seen:
#             uniq.append(t)
#             seen.add(t)

#     uniq.sort(key=len, reverse=True)
#     return uniq[:max_k]

# def generate_path_from_sentence(
#     G, embedding_tokenizer, embedding_model, node_embeddings, sentence: str
# ) -> str:
#     """Extract keywords from a sentence and generate a path connecting them."""
#     kws = extract_keywords(sentence, max_k=5)
#     if len(kws) < 2:
#         raise ValueError(f"Not enough keywords extracted from: {sentence!r}")

#     return generate_path(
#         G, embedding_tokenizer, embedding_model, node_embeddings,
#         keyword_1=kws[0], keyword_2=kws[1],
#         keyword_3=(kws[2] if len(kws) > 2 else None),
#         keyword_4=(kws[3] if len(kws) > 3 else None),
#         keyword_5=(kws[4] if len(kws) > 4 else None),
#     )

# def get_nodes_by_label(G, label: str, label_field: str = 'type', case_sensitive: bool = False):
#     """
#     Get all nodes in the graph by label.
#
#     Args:
#         G: NetworkX graph object
#         label: Target label value (e.g., "Gene", "Disease", "Protein", etc.)
#         label_field: Label field name (default 'type', could also be 'label', 'category', etc.)
#         case_sensitive: Whether to match case-sensitively (default False)
#
#     Returns:
#         nodes_with_label: List of nodes matching the criteria
#     """
#     nodes_with_label = []
#
#     # Preprocess label for comparison
#     search_label = label if case_sensitive else label.lower()
#
#     for node in G.nodes():
#         node_data = G.nodes[node]
#
#         # Get the node's type/label
#         node_label = node_data.get(label_field, '')
#
#         # Compare
#         if isinstance(node_label, str):
#             compare_label = node_label if case_sensitive else node_label.lower()
#             if compare_label == search_label:
#                 nodes_with_label.append(node)
#
#     return nodes_with_label

# def find_paths_to_label(
#     G,
#     embedding_tokenizer,
#     embedding_model,
#     node_embeddings,
#     source_keyword: str,
#     target_label: str,
#     label_field: str = 'type',
#     max_targets: Optional[int] = 20,
#     shortest_path: bool = True,
#     top_k: int = 5,
#     randomness_factor: float = 0,
#     num_random_waypoints: int = 0,
#     verbatim: bool = False,
#     data_dir: str = "./",
#     save_files: bool = False,
#     validate_nodes: bool = True,
#     use_llm_validation: bool = False,
#     llm_client = None,
#     llm_model: str = "gpt-4o-mini",
#     client_type: str = "openai",
# ):
#     """
#     Find paths from a single keyword to all nodes with a specified label.
#
#     ✅ New logic: find all paths first, then validate nodes in the paths.
#
#     Args:
#         ... (same parameters as above)
#
#     Returns:
#         paths_dict: {target_node: {"path_list": [...], "path_string": "...", "path_length": n, "validation": {...}}}
#         summary: Summary information including validation stats
#     """
#     # 1. Get all target label nodes (without pre-validation)
#     all_target_nodes = get_nodes_by_label_with_fallback(G, target_label, label_field=label_field)
    
#     if not all_target_nodes:
#         print(f"⚠️ Warning: No nodes found with label '{target_label}'")
#         return {}, {
#             "error": f"No nodes found with label '{target_label}' in field '{label_field}'",
#             "total_targets": 0,
#             "successful": 0,
#             "failed": 0
#         }
    
#     if verbatim:
#         print(f"\n{'='*60}")
#         print(f"📋 PATH FINDING (validation will be done AFTER path finding)")
#         print(f"{'='*60}")
#         print(f"Found {len(all_target_nodes)} nodes of type '{target_label}'")
#         print(f"{'='*60}\n")
    
#     # 2. Optional: limit the number
#     target_nodes = all_target_nodes
#     if max_targets and len(target_nodes) > max_targets:
#         target_nodes = random.sample(target_nodes, max_targets)
#         if verbatim:
#             print(f"Limited to {max_targets} nodes for path calculation")
    
#     # 3. ✅ Calculate all paths first (no validation)
#     paths_dict = {}
#     failed_targets = []
    
#     if verbatim:
#         print(f"\n🔍 Step 1: Finding paths to {len(target_nodes)} target nodes...")
    
#     for i, target_node in enumerate(target_nodes, 1):
#         if verbatim and i % 10 == 0:
#             print(f"   Progress: {i}/{len(target_nodes)}")
        
#         try:
#             path_list, path_string = create_path(
#                 G,
#                 embedding_tokenizer,
#                 embedding_model,
#                 node_embeddings,
#                 keyword_1=source_keyword,
#                 keyword_2=target_node,
#                 shortest_path=shortest_path,
#                 top_k=top_k,
#                 randomness_factor=randomness_factor,
#                 num_random_waypoints=num_random_waypoints,
#                 verbatim=False,
#                 data_dir=data_dir,
#                 save_files=save_files
#             )
            
#             path_length = len([x for x in path_list if x != ""])
#             paths_dict[target_node] = {
#                 "path_list": path_list,
#                 "path_string": path_string,
#                 "path_length": path_length,
#                 "validated": False,  # Marked as not validated
#                 "validation_result": None
#             }
            
#         except Exception as e:
#             failed_targets.append((target_node, str(e)))
#             if verbatim:
#                 print(f"   ❌ Path to {target_node} failed: {e}")
    
#     if verbatim:
#         print(f"\n✅ Found {len(paths_dict)} paths")
    
#     # 4. ✅ Sort by path length, only validate the shortest paths
#     if validate_nodes and use_llm_validation and llm_client and paths_dict:
#         if verbatim:
#             print(f"\n🤖 Step 2: Validating nodes in shortest paths using {llm_model}...\n")
        
#         # Sort by path length
#         sorted_paths = sorted(paths_dict.items(), key=lambda x: x[1]['path_length'])

#         # Only validate the top N shortest paths (default 50)
#         num_paths_to_validate = min(30, len(sorted_paths))
#         validated_count = 0
#         invalid_count = 0
        
#         for i, (target_node, path_info) in enumerate(sorted_paths[:num_paths_to_validate], 1):
#             # === Delete this line ===
#             # if verbatim:
#             #     print(f"\n   Validating path {i}/{num_paths_to_validate} to '{target_node}'...")

#             # Concise progress display (update on the same line)
#             if verbatim:
#                 print(f"\rValidating: {i}/{num_paths_to_validate} | ✅ Valid: {validated_count} | ❌ Invalid: {invalid_count}", 
#                     end='', flush=True)
            
#             # Extract target type nodes from the path
#             target_type_nodes = extract_target_type_nodes_from_path(
#                 path_info['path_list'], 
#                 G, 
#                 target_label, 
#                 label_field
#             )
            
#             if not target_type_nodes:
#                 # No nodes to validate, mark as valid
#                 paths_dict[target_node]['validated'] = True
#                 paths_dict[target_node]['validation_result'] = {
#                     "valid": True,
#                     "reason": "No target type nodes to validate"
#                 }
#                 validated_count += 1
#                 continue
            
#             # Validate these nodes
#             is_valid, reason, node_validations = validate_path_nodes_with_llm(
#                 path_string=path_info['path_string'],
#                 node_names=target_type_nodes,
#                 node_type=target_label,
#                 llm_client=llm_client,
#                 model=llm_model,
#                 client_type=client_type,
#                 verbatim=False  # Disable verbose output
#             )
            
#             # Save validation results
#             paths_dict[target_node]['validated'] = True
#             paths_dict[target_node]['validation_result'] = {
#                 "valid": is_valid,
#                 "reason": reason,
#                 "node_validations": node_validations
#             }
            
#             if is_valid:
#                 validated_count += 1
#             else:
#                 invalid_count += 1
#                 # === Delete this line ===
#                 # if verbatim:
#                 #     print(f"   ❌ Path invalid: {reason}")
        
#         # New line (end progress bar)
#         if verbatim:
#             print()  # New line
        
#         if verbatim:
#             print(f"\n{'='*60}")
#             print(f"VALIDATION COMPLETE")
#             print(f"{'='*60}")
#             print(f"Validated: {num_paths_to_validate} paths")
#             print(f"✅ Valid: {validated_count}")
#             print(f"❌ Invalid: {invalid_count}")
#             print(f"⏭️  Not validated: {len(sorted_paths) - num_paths_to_validate} (lower priority paths)")
#             print(f"{'='*60}")
    
#     # 5. Return results and statistics
#     summary = {
#         "validation_method": "post-path-llm" if (validate_nodes and use_llm_validation) else "none",
#         "total_found": len(all_target_nodes),
#         "paths_calculated": len(target_nodes),
#         "successful_paths": len(paths_dict),
#         "failed_paths": len(failed_targets),
#         "validated_paths": sum(1 for p in paths_dict.values() if p.get('validated', False)),
#         "valid_paths": sum(1 for p in paths_dict.values() 
#                           if p.get('validated', False) and p.get('validation_result', {}).get('valid', False)),
#         "invalid_paths": sum(1 for p in paths_dict.values() 
#                             if p.get('validated', False) and not p.get('validation_result', {}).get('valid', False)),
#         "failed_details": failed_targets[:10],
#         "validation_enabled": validate_nodes and use_llm_validation,
#     }
    
#     if verbatim:
#         print(f"\n{'='*60}")
#         print(f"✅ PATH FINDING AND VALIDATION COMPLETE")
#         print(f"{'='*60}")
#         print(f"Total target nodes found: {summary['total_found']}")
#         print(f"Successful paths: {summary['successful_paths']}/{summary['paths_calculated']}")
#         if summary['validation_enabled']:
#             print(f"Validated paths: {summary['validated_paths']}")
#             print(f"  - Valid: {summary['valid_paths']}")
#             print(f"  - Invalid: {summary['invalid_paths']}")
#         print(f"{'='*60}")
    
#     return paths_dict, summary

# def visualize_paths_to_label(paths_dict, summary, top_n: int = 10):
#     """
#     Visualize path results (sorted by path length, display top N)
#     """
#     if not paths_dict:
#         print("No paths found!")
#         return
    
#     # Sort by path length
#     sorted_paths = sorted(
#         paths_dict.items(), 
#         key=lambda x: x[1]['path_length']
#     )
    
#     print(f"\n{'='*60}")
#     print(f"Found {summary['successful']}/{summary['total_targets']} paths")
#     print(f"{'='*60}\n")
    
#     for i, (target, info) in enumerate(sorted_paths[:top_n], 1):
#         print(f"{i}. Path to '{target}' (length: {info['path_length']})")
#         print(f"   {info['path_string']}")
#         print()
    
#     if summary['failed'] > 0:
#         print(f"\n⚠️  {summary['failed']} target nodes could not find paths")

# def export_paths_to_csv(paths_dict, output_file: str = "paths_to_label.csv"):
#     """
#     Export path results to CSV file
#     """
#     import csv
    
#     with open(output_file, 'w', newline='', encoding='utf-8') as f:
#         writer = csv.writer(f)
#         writer.writerow(['Target Node', 'Path Length', 'Path String'])
        
#         for target, info in sorted(
#             paths_dict.items(), 
#             key=lambda x: x[1]['path_length']
#         ):
#             writer.writerow([
#                 target,
#                 info['path_length'],
#                 info['path_string']
#             ])
    
#     print(f"✅ Paths exported to {output_file}")

# ## Control the number of checking nodes
# def find_shortest_paths_to_label_for_agent(
#     G,
#     embedding_tokenizer,
#     embedding_model,
#     node_embeddings,
#     source_keyword: str,
#     target_label: str,
#     max_results: int = 5,  # Return 30 valid paths
#     label_field: str = "type",
#     validate_nodes: bool = True,
#     use_llm_validation: bool = True,
#     llm_client = None,
#     llm_model: str = "gpt-4o-mini",
#     client_type: str = "openai",
# ) -> str:
#     """Find shortest paths from a SPECIFIC entity to ALL entities of a certain TYPE.
    
#     ✅ NEW: Validates nodes AFTER finding paths, not before!
#     This is much more efficient as we only validate nodes in the shortest paths.
#     """
    
#     try:
#         category_words = {
#             # ==================== A ====================
#             "acid", "age", "algorithm", "amino", "aminoacid", "anatomical",
#             "anatomicalstructure", "anatomy", "anomaly", "antibody", "antigen",
#             "artery", "assay", "atlas",
            
#             # ==================== B ====================
#             "bacteria", "bacterium", "benchmark", "biomarker", "bone", "brain",
#             "bundle",
            
#             # ==================== C ====================
#             "cancer", "cancertype", "case", "catheter", "cell", "cellcluster",
#             "cellline", "celltype", "channel", "characteristic", "chemical",
#             "chromosomal", "chromosome", "classification", "clinical",
#             "clinicalfactor", "clinicaltrial", "clinicalvariable", "cluster",
#             "cohort", "combination", "complex", "complication", "component",
#             "composite", "compound", "condition", "cost", "criteria", "cultivar",
#             "cytokine",
            
#             # ==================== D ====================
#             "dna", "data", "database", "dataset", "definition", "demographic",
#             "device", "diet", "disease", "domain", "dosage", "drug", "duct",
            
#             # ==================== E ====================
#             "effect", "embryo", "enzyme", "ethnicity", "evidence", "exosome",
#             "experiment",
            
#             # ==================== F ====================
#             "facility", "factor", "feature", "fiber", "field", "fluid", "food",
#             "formula", "formulation", "function",
            
#             # ==================== G ====================
#             "gender", "gene", "genetic", "genome", "genomic", "genotype", "genus",
#             "gland", "grade", "graph", "ground", "group", "groups",
            
#             # ==================== H ====================
#             "hardware", "hormone", "hospital",
            
#             # ==================== I ====================
#             "image", "imaging", "imagingtechnique", "information", "injury",
#             "insurance", "involvement", "ion",
            
#             # ==================== K ====================
#             "karyotype",
            
#             # ==================== L ====================
#             "laboratory", "laser", "layer", "lineage", "lncrna", "location",
#             "loss", "lymph", "lymphnode",
            
#             # ==================== M ====================
#             "marker", "material", "matrix", "measurement", "medical",
#             "medicalprocedure", "medicalprofessional", "metabolite", "metastasis",
#             "method", "metric", "microbiome", "microbiota", "microorganism",
#             "modality", "model", "modification", "molecule", "mouse", "muscle",
#             "mutation",
            
#             # ==================== N ====================
#             "n/a", "nanoparticle", "nerve", "network", "neuron", "neurotransmitter",
#             "node", "nucleic", "nucleicacid", "nucleus",
            
#             # ==================== O ====================
#             "object", "ontology", "organ", "organism", "organization", "outcome",
            
#             # ==================== P ====================
#             "parameter", "parasite", "particle", "pathway", "patient",
#             "patientgroup", "peptide", "percentage", "phenomenon", "phenotype",
#             "phylum", "plant", "plasmid", "population", "prevalence", "procedure",
#             "process", "product", "profession", "protein", "protocol", "publication",
            
#             # ==================== Q ====================
#             "questionnaire",
            
#             # ==================== R ====================
#             "rna", "rnai", "race", "receptor", "reference", "region",
#             "representation", "research", "result", "risk", "riskcategory",
#             "riskfactor", "riskscore", "river", "robot", "relation",
            
#             # ==================== S ====================
#             "sample", "section", "service", "setting", "sex", "signal", "size",
#             "sling", "socioeconomic", "socioeconomicstatus", "software", "space",
#             "specialist", "specialty", "species", "specimen", "spinalsegment",
#             "stage", "statistical", "stemcell", "stent", "structure", "study",
#             "subtype", "surgeon", "surgery", "surgical", "surgicalprocedure",
#             "symptom", "syndrome", "system",
            
#             # ==================== T ====================
#             "target", "task", "technique", "temperature", "test", "therapy",
#             "timeperiod", "timepoint", "tissue", "token", "tool", "trait",
#             "transcript", "transform", "treatment", "truth", "tumor",
            
#             # ==================== U ====================
#             "urethra",
            
#             # ==================== V ====================
#             "vaccine", "variant", "vector", "vein", "vertebra", "vessel", "virus",
#             "virus-like",
#         }
        
#         if source_keyword.lower().strip() in category_words:
#             return (
#                 f"❌ ERROR: '{source_keyword}' is a CATEGORY, not a specific entity.\n\n"
#                 f"Please use a SPECIFIC entity name as source_keyword."
#             )
        
#         invalid_labels = {"knowledge path", "research", "idea", "concept"}
#         if target_label.lower() in invalid_labels:
#             return (
#                 f"❌ ERROR: '{target_label}' is not a valid node type in the graph.\n\n"
#                 f"Valid node types are: Gene, Disease, Protein, Drug, Compound, CellType, Process, Metabolite"
#             )
        
#         if use_llm_validation and llm_client is None:
#             print("⚠️ Warning: LLM validation requested but no llm_client provided.")
#             print("   Falling back to no validation.")
#             use_llm_validation = False
        
#         # ✅ Call the new post-validation logic
#         paths_dict, summary = find_paths_to_label(
#             G,
#             embedding_tokenizer,
#             embedding_model,
#             node_embeddings,
#             source_keyword=source_keyword,
#             target_label=target_label,
#             label_field=label_field,
#             max_targets=max_results * 2,  # Find more paths for validation
#             shortest_path=True,
#             verbatim=True,
#             validate_nodes=validate_nodes,
#             use_llm_validation=use_llm_validation,
#             llm_client=llm_client,
#             llm_model=llm_model,
#             client_type=client_type
#         )
        
#         if not paths_dict:
#             return (
#                 f"No paths found from '{source_keyword}' to nodes with label '{target_label}'.\n\n"
#                 f"Total nodes found: {summary.get('total_found', 0)}\n"
#                 f"Suggestion: Try different entity names or check available node types."
#             )
        
#         # ✅ Prioritize showing validated and valid paths
#         if summary.get('validation_enabled'):
#             validated_valid = [(node, info) for node, info in paths_dict.items()
#                               if info.get('validated') and info.get('validation_result', {}).get('valid')]
#             validated_invalid = [(node, info) for node, info in paths_dict.items()
#                                 if info.get('validated') and not info.get('validation_result', {}).get('valid')]
#             not_validated = [(node, info) for node, info in paths_dict.items()
#                             if not info.get('validated')]
            
#             validated_valid.sort(key=lambda x: x[1]['path_length'])
#             not_validated.sort(key=lambda x: x[1]['path_length'])
            
#             if validated_valid:
#                 sorted_paths = validated_valid[:max_results]
#                 result_source = "validated and confirmed valid"
#             else:
#                 sorted_paths = not_validated[:max_results]
#                 result_source = "not yet validated (assumed valid)"
#         else:
#             sorted_paths = sorted(paths_dict.items(), key=lambda x: x[1]['path_length'])[:max_results]
#             result_source = "no validation performed"
        
#         # ===== Output format =====
#         result_lines = []
        
#         # 1. Concise title and validation summary
#         if summary.get('validation_enabled'):
#             result_lines.append(
#                 f"✅ Found {summary['valid_paths']}/{summary['validated_paths']} valid paths "
#                 f"from '{source_keyword}' to '{target_label}' (validated by {llm_model})\n"
#             )
#         else:
#             result_lines.append(
#                 f"✅ Found {len(sorted_paths)} paths from '{source_keyword}' to '{target_label}'\n"
#             )
        
#         # 2. Show only the top 3 paths (preview)
#         display_count = min(3, len(sorted_paths))
#         result_lines.append(f"Top {display_count} shortest paths:")
        
#         for i, (target, info) in enumerate(sorted_paths[:display_count], 1):
#             validation_mark = ""
#             if info.get('validated'):
#                 validation_mark = " ✅" if info.get('validation_result', {}).get('valid') else " ❌"
            
#             result_lines.append(
#                 f"\n{i}. {target}{validation_mark} (length: {info['path_length']})"
#             )
#             result_lines.append(f"   {info['path_string']}")
        
#         # 3. Selected path details
#         if sorted_paths:
#             selected_target, selected_info = sorted_paths[0]
#             result_lines.append(f"\n{'='*60}")
#             result_lines.append(f"📍 SELECTED PATH (Primary Focus):")
#             result_lines.append(f"  • Target: '{selected_target}'")
#             if selected_info.get('validated'):
#                 validation_result = selected_info.get('validation_result', {})
#                 if validation_result.get('valid'):
#                     result_lines.append(f"  • Status: ✅ Validated by {llm_model}")
#                 else:
#                     result_lines.append(f"  • Status: ❌ {validation_result.get('reason', 'Invalid')}")
#             result_lines.append(f"  • Length: {selected_info['path_length']} nodes")
#             result_lines.append(f"  • Path: {selected_info['path_string']}")
#             result_lines.append(f"{'='*60}\n")
        
#         # 4. All validated and valid paths
#         result_lines.append(f"\n{'='*70}")
#         result_lines.append(f"📋 ALL {len(sorted_paths)} VALIDATED PATHS:")
#         result_lines.append(f"{'='*70}")
        
#         for i, (target, info) in enumerate(sorted_paths, 1):
#             validation_mark = ""
#             if info.get('validated'):
#                 validation_mark = " ✅" if info.get('validation_result', {}).get('valid') else " ❌"
            
#             result_lines.append(
#                 f"\n{i}. {target}{validation_mark} (length: {info['path_length']})"
#             )
#             result_lines.append(f"   {info['path_string']}")
        
#         result_lines.append(f"\n{'='*70}")
        
#         # 5. Explicitly request analysis of 10 paths
#         result_lines.append(f"\n{'='*70}")
#         result_lines.append(f"📋 INSTRUCTIONS FOR ONTOLOGIST:")
#         result_lines.append(f"{'='*70}")
#         result_lines.append(f"You have received {len(sorted_paths)} candidate paths above.\n")
        
#         # Determine number to analyze
#         num_to_analyze = min(10, len(sorted_paths))
        
#         result_lines.append(f"Task: Select and analyze the TOP {num_to_analyze} most scientifically interesting paths.\n")
#         result_lines.append(f"Required Format:")
#         result_lines.append(f"1. PATH SELECTION: List all {num_to_analyze} selected paths with brief selection rationale")
#         result_lines.append(f"2. DETAILED ANALYSIS: For each of the {num_to_analyze} paths, provide:")
#         result_lines.append(f"   - Key Definitions (2-4 sentences per term)")
#         result_lines.append(f"   - Relationship Analysis (2-3 sentences per relationship)")
#         result_lines.append(f"   - Clinical/Research Significance (2-3 sentences)")
#         result_lines.append(f"\nSelection Criteria (prioritize paths with):")
#         result_lines.append(f"  • Novel biological connections")
#         result_lines.append(f"  • Clear therapeutic or biomarker potential")
#         result_lines.append(f"  • Strong clinical relevance")
#         result_lines.append(f"  • Mechanistic clarity and testability")
#         result_lines.append(f"  • Diversity across different mechanisms")
#         result_lines.append(f"\nNote: Ensure all {num_to_analyze} paths are analyzed in detail.")
#         result_lines.append(f"{'='*70}\n")
        
#         return "\n".join(result_lines)
    
#     except Exception as e:
#         error_msg = (
#             f"❌ Error finding paths from '{source_keyword}' to '{target_label}':\n"
#             f"{str(e)}\n\n"
#             f"Error type: {type(e).__name__}"
#         )
#         print(f"[ERROR] {error_msg}")
#         return error_msg
# # ==================== LLM-Based Node Validation with OpenAI ====================

# import json
# from typing import List, Dict, Tuple, Optional
# import openai

# def validate_path_nodes_with_llm(
#     path_string: str,
#     node_names: List[str],
#     node_type: str,
#     llm_client,
#     model: str = "gpt-4o-mini",
#     client_type: str = "openai",
#     verbatim: bool = False
# ) -> Tuple[bool, str, Dict[str, str]]:
#     """
#     Validate nodes in a single path.
#
#     Args:
#         path_string: Complete path string
#         node_names: List of node names to validate in the path
#         node_type: Node type (e.g., "Gene")
#         llm_client: LLM client
#         model: Model name
#         client_type: 'openai' or 'anthropic'
#         verbatim: Whether to output detailed information (parameter retained but unused, for debugging)
#
#     Returns:
#         is_valid: Whether the path is valid (all critical nodes are valid)
#         reason: Explanation if invalid
#         node_validations: Validation results for each node
#     """
    
#     if not node_names:
#         return True, "No nodes to validate", {}
    
#     try:
#         # Create validation prompt
#         prompt = create_path_validation_prompt(node_names, node_type, path_string)
        
#         # Call LLM
#         if client_type == "openai":
#             response_text = call_openai(llm_client, model, prompt)
#         else:
#             response_text = call_anthropic(llm_client, model, prompt)
        
#         # Parse response
#         node_validations = parse_path_validation_response(response_text, node_names)
        
#         # Check if all target type nodes are valid
#         invalid_nodes = [node for node, result in node_validations.items() 
#                         if not result.get("valid", False)]
        
#         if invalid_nodes:
#             is_valid = False
#             reasons = [f"{node}: {node_validations[node].get('reason', 'Unknown')}" 
#                       for node in invalid_nodes]
#             reason = f"Invalid nodes found: {'; '.join(reasons)}"
#         else:
#             is_valid = True
#             reason = "All nodes validated successfully"
        
#         return is_valid, reason, node_validations
    
#     except Exception as e:
#         # Conservatively consider path valid on error
#         return True, f"Validation error: {str(e)}", {}

# def call_openai(client, model: str, prompt: str) -> str:
#     """
#     Call OpenAI API
#     """
#     response = client.chat.completions.create(
#         model=model,
#         messages=[
#             {
#                 "role": "system",
#                 "content": "You are a biomedical knowledge expert specializing in validating entity names. Always respond with valid JSON only."
#             },
#             {
#                 "role": "user",
#                 "content": prompt
#             }
#         ],
#         temperature=0,  # Use temperature=0 for consistent validation
#         max_tokens=4096,
#         response_format={"type": "json_object"}  # ✅ Force JSON output
#     )
    
#     return response.choices[0].message.content

# def call_anthropic(client, model: str, prompt: str) -> str:
#     """
#     Call Anthropic API
#     """
#     response = client.messages.create(
#         model=model,
#         max_tokens=4096,
#         temperature=0,
#         messages=[{
#             "role": "user",
#             "content": prompt
#         }]
#     )
    
#     return response.content[0].text

# def create_path_validation_prompt(node_names: List[str], node_type: str, path_string: str) -> str:
#     """
#     Create prompt for path validation
#     """
    
#     type_criteria = {
#         "Gene": {
#             "valid_examples": [
#                 "BRCA1", "TP53", "EGFR", "IL6", "VEGFA"
#             ],
#             "invalid_examples": [
#                 "neuroinflammatory genes",
#                 "genes and synaptic biology",
#                 "high ENO1 expression",
#                 "gene collection"
#             ],
#         },
#         "Protein": {
#             "valid_examples": ["p53 protein", "insulin", "hemoglobin"],
#             "invalid_examples": ["proteins in the cell", "protein collection"],
#         },
#         "Disease": {
#             "valid_examples": ["breast cancer", "Alzheimer's disease", "type 2 diabetes"],
#             "invalid_examples": ["diseases of the heart", "disease collection"],
#         },
#         "Drug": {
#             "valid_examples": ["aspirin", "metformin", "cisplatin"],
#             "invalid_examples": ["cancer drugs", "drug collection"],
#         }
#     }
    
#     criteria = type_criteria.get(node_type, {
#         "valid_examples": ["specific entity"],
#         "invalid_examples": ["generic description"],
#     })
    
#     prompt = f"""You are validating nodes in a knowledge graph path. The path is:

# {path_string}

# Focus on validating these {node_type} nodes:
# {chr(10).join(f'{i+1}. "{name}"' for i, name in enumerate(node_names))}

# VALIDATION CRITERIA:
# - VALID {node_type} examples: {', '.join(criteria['valid_examples'])}
# - INVALID examples (reject these): {', '.join(criteria['invalid_examples'])}

# RULES:
# 1. Node names with "genes", "proteins", "collection", "expression", "associated" → INVALID
# 2. Phrases with "and", "or", numbers like "1159 sets" → INVALID
# 3. Modifiers like "high", "low", "increased" → INVALID
# 4. Real {node_type} names are concise and specific

# For each node, determine if it's a VALID {node_type} or INVALID.

# OUTPUT FORMAT (JSON only):
# {{
#     "node_validations": [
#         {{"name": "node1", "valid": true, "reason": "Valid gene symbol"}},
#         {{"name": "node2", "valid": false, "reason": "Descriptive phrase, not specific gene"}},
#         ...
#     ]
# }}

# Return ONLY valid JSON."""

#     return prompt

# def parse_validation_response(
#     response_text: str,
#     original_nodes: List[str]
# ) -> Tuple[List[str], List[str], Dict[str, str]]:
#     """
#     Parse LLM validation response
    
#     Returns:
#         valid_nodes: List of valid node names
#         invalid_nodes: List of invalid node names  
#         reasons: Dict mapping invalid node -> reason
#     """
#     try:
#         # Extract JSON from response (handle potential markdown formatting)
#         response_text = response_text.strip()
        
#         # Remove markdown code blocks if present
#         if response_text.startswith("```json"):
#             response_text = response_text[7:]
#         if response_text.startswith("```"):
#             response_text = response_text[3:]
#         if response_text.endswith("```"):
#             response_text = response_text[:-3]
#         response_text = response_text.strip()
        
#         result = json.loads(response_text)
        
#         valid_nodes = result.get("valid", [])
#         invalid_entries = result.get("invalid", [])
        
#         invalid_nodes = []
#         reasons = {}
        
#         for entry in invalid_entries:
#             if isinstance(entry, dict):
#                 node_name = entry.get("name")
#                 reason = entry.get("reason", "No reason provided")
#                 invalid_nodes.append(node_name)
#                 reasons[node_name] = reason
#             elif isinstance(entry, str):
#                 invalid_nodes.append(entry)
#                 reasons[entry] = "Marked as invalid by LLM"
        
#         return valid_nodes, invalid_nodes, reasons
    
#     except json.JSONDecodeError as e:
#         print(f"⚠️ Warning: Could not parse LLM response as JSON: {e}")
#         print(f"Response text (first 500 chars): {response_text[:500]}")
#         # On parse error, conservatively mark all as valid
#         return original_nodes, [], {}
#     except Exception as e:
#         print(f"⚠️ Warning: Error parsing validation response: {e}")
#         return original_nodes, [], {}

# def create_find_paths_to_label_tool(
#     G,
#     embedding_tokenizer,
#     embedding_model,
#     node_embeddings,
#     llm_client=None,
#     use_llm_validation=True,
#     llm_model="gpt-4o-mini",
#     client_type="openai"
# ):
#     """
#     Create a pre-configured find_shortest_paths_to_label_for_agent function.
#
#     This factory function returns a tool function bound with graph and LLM client,
#     ready to be passed to an agent.
#     """
    
#     def tool_function(
#         source_keyword: Annotated[str, "A SPECIFIC entity name from the graph"],
#         target_label: Annotated[str, "A node TYPE that exists in the graph"],
#         max_results: Annotated[int, "Maximum number of shortest paths to return"] = 30,
#         label_field: Annotated[str, "Field name for node labels in the graph"] = "type",
#         validate_nodes: Annotated[bool, "Whether to filter out invalid node names"] = True,
#     ) -> str:
#         """Find shortest paths from a SPECIFIC entity to ALL entities of a certain TYPE."""
#         return find_shortest_paths_to_label_for_agent(
#             G=G,
#             embedding_tokenizer=embedding_tokenizer,
#             embedding_model=embedding_model,
#             node_embeddings=node_embeddings,
#             source_keyword=source_keyword,
#             target_label=target_label,
#             max_results=max_results,
#             label_field=label_field,
#             validate_nodes=validate_nodes,
#             use_llm_validation=use_llm_validation,
#             llm_client=llm_client,
#             llm_model=llm_model,
#             client_type=client_type,
#         )
    
#     return tool_function

# def parse_path_validation_response(response_text: str, node_names: List[str]) -> Dict[str, Dict]:
#     """
#     Parse path validation response
    
#     Returns:
#         Dict mapping node_name -> {"valid": bool, "reason": str}
#     """
#     try:
#         response_text = response_text.strip()
#         if response_text.startswith("```json"):
#             response_text = response_text[7:]
#         if response_text.startswith("```"):
#             response_text = response_text[3:]
#         if response_text.endswith("```"):
#             response_text = response_text[:-3]
#         response_text = response_text.strip()
        
#         result = json.loads(response_text)
#         validations = result.get("node_validations", [])
        
#         node_results = {}
#         for item in validations:
#             node_name = item.get("name")
#             node_results[node_name] = {
#                 "valid": item.get("valid", True),
#                 "reason": item.get("reason", "No reason provided")
#             }
        
#         # Default to valid for nodes without returned results
#         for node in node_names:
#             if node not in node_results:
#                 node_results[node] = {"valid": True, "reason": "Not evaluated"}
        
#         return node_results
    
#     except Exception as e:
#         print(f"⚠️ Warning: Could not parse validation response: {e}")
#         # Default all nodes to valid on error
#         return {node: {"valid": True, "reason": "Parse error"} for node in node_names}

# def extract_target_type_nodes_from_path(path_list: List[str], G, target_label: str, label_field: str = 'type') -> List[str]:
#     """
#     Extract nodes of a specific type from a path.
#
#     Args:
#         path_list: List of path nodes
#         G: Graph object
#         target_label: Target node type
#         label_field: Label field name
#
#     Returns:
#         List of nodes of that type
#     """
#     target_nodes = []
#     for node in path_list:
#         if node and node != "":  # Skip empty strings
#             node_data = G.nodes.get(node, {})
#             node_type = node_data.get(label_field, "")
#             if node_type.lower() == target_label.lower():
#                 target_nodes.append(node)
#     return target_nodes

# # HypothesisGeneration/utils.py

# import requests
# import time
# from xml.etree import ElementTree as ET
# import os
# from typing import Optional, Dict, List
# from requests.adapters import HTTPAdapter
# from urllib3.util.retry import Retry

# # ==================== PubMed Search ====================
# def pubmed_search(query: str, max_results: int = 10) -> str:
#     """
#     Search PubMed biomedical literature.
#
#     Args:
#         query: Search query
#         max_results: Maximum number of results
#
#     Returns:
#         Formatted search results
#     """
#     try:
#         # Step 1: Search and retrieve PubMed IDs
#         search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
#         search_params = {
#             "db": "pubmed",
#             "term": query,
#             "retmax": max_results,
#             "retmode": "json",
#             "sort": "relevance"
#         }
        
#         response = requests.get(search_url, params=search_params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         id_list = data.get("esearchresult", {}).get("idlist", [])
        
#         if not id_list:
#             return f"No results found in PubMed for query: {query}"
        
#         # Step 2: Fetch detailed information
#         time.sleep(0.4)  # NCBI courtesy delay
        
#         fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
#         fetch_params = {
#             "db": "pubmed",
#             "id": ",".join(id_list[:max_results]),
#             "retmode": "xml"
#         }
        
#         fetch_response = requests.get(fetch_url, params=fetch_params, timeout=10)
#         fetch_response.raise_for_status()
        
#         # Parse XML
#         root = ET.fromstring(fetch_response.content)
        
#         results = []
#         for article in root.findall(".//PubmedArticle"):
#             try:
#                 # Extract title
#                 title_elem = article.find(".//ArticleTitle")
#                 title = title_elem.text if title_elem is not None else "No title"
                
#                 # Extract abstract
#                 abstract_elem = article.find(".//AbstractText")
#                 abstract = abstract_elem.text if abstract_elem is not None else "No abstract"
                
#                 # Extract PMID
#                 pmid_elem = article.find(".//PMID")
#                 pmid = pmid_elem.text if pmid_elem is not None else "Unknown"
                
#                 # Extract year
#                 year_elem = article.find(".//PubDate/Year")
#                 year = year_elem.text if year_elem is not None else "Unknown"
                
#                 results.append({
#                     "title": title,
#                     "abstract": abstract[:300] + "..." if len(abstract) > 300 else abstract,
#                     "year": year,
#                     "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
#                     "pmid": pmid,
#                     "source": "PubMed"
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"PubMed Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Year: {paper['year']}\n"
#             output += f"   PMID: {paper['pmid']}\n"
#             output += f"   URL: {paper['url']}\n"
#             output += f"   Abstract: {paper['abstract']}\n\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching PubMed: {str(e)}"

# # ==================== arXiv Search ====================
# def arxiv_search(query: str, max_results: int = 10) -> str:
#     """
#     Search arXiv preprints
    
#     Args:
#         query: Search query
#         max_results: Maximum number of results
    
#     Returns:
#         Formatted search results
#     """
#     try:
#         url = "http://export.arxiv.org/api/query"
#         params = {
#             "search_query": f"all:{query}",
#             "start": 0,
#             "max_results": max_results,
#             "sortBy": "relevance",
#             "sortOrder": "descending"
#         }
        
#         response = requests.get(url, params=params, timeout=10)
#         response.raise_for_status()
        
#         # Parse XML
#         root = ET.fromstring(response.content)
        
#         # Namespaces
#         ns = {
#             'atom': 'http://www.w3.org/2005/Atom',
#             'arxiv': 'http://arxiv.org/schemas/atom'
#         }
        
#         entries = root.findall('atom:entry', ns)
        
#         if not entries:
#             return f"No results found in arXiv for query: {query}"
        
#         results = []
#         for entry in entries:
#             try:
#                 title = entry.find('atom:title', ns).text.strip()
#                 summary = entry.find('atom:summary', ns).text.strip()
#                 published = entry.find('atom:published', ns).text[:10]  # YYYY-MM-DD
#                 arxiv_id = entry.find('atom:id', ns).text.split('/abs/')[-1]
                
#                 results.append({
#                     "title": title,
#                     "abstract": summary[:300] + "..." if len(summary) > 300 else summary,
#                     "year": published[:4],
#                     "url": f"https://arxiv.org/abs/{arxiv_id}",
#                     "arxiv_id": arxiv_id,
#                     "source": "arXiv"
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"arXiv Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Year: {paper['year']}\n"
#             output += f"   arXiv ID: {paper['arxiv_id']}\n"
#             output += f"   URL: {paper['url']}\n"
#             output += f"   Abstract: {paper['abstract']}\n\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching arXiv: {str(e)}"

# # ==================== OpenAlex Search ====================
# def openalex_search(query: str, max_results: int = 10) -> str:
#     """
#     Search OpenAlex academic database
    
#     Args:
#         query: Search query
#         max_results: Maximum number of results
    
#     Returns:
#         Formatted search results
#     """
#     try:
#         url = "https://api.openalex.org/works"
#         params = {
#             "search": query,
#             "per_page": max_results,
#             "mailto": "your-email@example.com"  # Courtesy parameter, improves rate limit
#         }
        
#         response = requests.get(url, params=params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         results_list = data.get("results", [])
        
#         if not results_list:
#             return f"No results found in OpenAlex for query: {query}"
        
#         results = []
#         for work in results_list:
#             try:
#                 title = work.get("title", "No title")
#                 abstract = work.get("abstract", "No abstract available")
#                 year = work.get("publication_year", "Unknown")
#                 doi = work.get("doi", "")
#                 url = work.get("doi", work.get("id", ""))
                
#                 results.append({
#                     "title": title,
#                     "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
#                     "year": str(year),
#                     "url": url,
#                     "source": "OpenAlex"
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"OpenAlex Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Year: {paper['year']}\n"
#             output += f"   URL: {paper['url']}\n"
#             if paper['abstract'] != "No abstract available":
#                 output += f"   Abstract: {paper['abstract']}\n"
#             output += "\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching OpenAlex: {str(e)}"

# def multi_source_literature_search(
#     query: str,
#     sources: List[str] = None,
#     max_results_per_source: int = 5
# ) -> str:
#     """
#     Search literature from multiple sources and merge results
    
#     Args:
#         query: Search query
#         sources: Data source list, options:
#                 ["semantic_scholar", "pubmed", "arxiv", "openalex", 
#                  "europepmc", "biorxiv", "crossref"]
#         max_results_per_source: Maximum results per source
    
#     Returns:
#         Merged search results
#     """
#     if sources is None:
#         sources = ["semantic_scholar", "pubmed"]
    
#     all_results = []
    
#     # ✅ Add new search functions
#     search_functions = {
#         "semantic_scholar": semanticscholar_search,
#         "semanticscholar": semanticscholar_search,
#         "pubmed": pubmed_search,
#         "arxiv": arxiv_search,
#         "openalex": openalex_search,
#         "europepmc": europepmc_search,        # ✅ New
#         "europe_pmc": europepmc_search,       # ✅ New (alias)
#         "biorxiv": biorxiv_search,            # ✅ New
#         "medrxiv": biorxiv_search,            # ✅ New (uses same function)
#         "crossref": crossref_search,          # ✅ New
#     }
    
#     for source in sources:
#         # Normalize source name
#         source_normalized = source.lower().replace("_", "").replace("-", "")
        
#         # Find matching search function
#         search_func = None
#         for key, func in search_functions.items():
#             key_normalized = key.lower().replace("_", "").replace("-", "")
#             if key_normalized == source_normalized:
#                 search_func = func
#                 break
        
#         if search_func:
#             try:
#                 print(f"🔍 Searching {source}...")
#                 result = search_func(query, max_results=max_results_per_source)
#                 all_results.append(result)
                
#                 # Add courtesy delay
#                 time.sleep(1.5)
                
#             except Exception as e:
#                 print(f"⚠️ Error searching {source}: {e}")
#                 all_results.append(f"Error searching {source}: {str(e)}")
#         else:
#             print(f"⚠️ Unknown source: {source}")
#             print(f"   Available sources: {list(search_functions.keys())}")
    
#     if not all_results:
#         return f"No results found across any sources for query: {query}"
    
#     combined = "\n\n" + "="*80 + "\n\n".join(all_results)
#     return combined

# def _synthesize_voting(self, results: List, scores: List) -> Dict:
#     """Simple voting synthesis"""
    
#     mean_score = np.mean(scores)
#     consensus_score = round(mean_score)
    
#     if consensus_score >= 7:
#         recommendation = "APPROVE"
#     elif consensus_score >= 5:
#         recommendation = "REVISE"
#     else:
#         recommendation = "REJECT"
    
#     # ✅ Collect all references found by reviewers
#     all_references = []
#     reference_by_source = {}
    
#     for result in results:
#         reviewer_role = result.get('reviewer_role', 'unknown')
#         refs = result.get('references', [])
        
#         if refs:
#             reference_by_source[reviewer_role] = refs
#             all_references.extend(refs)
    
#     # ✅ Deduplicate (preserve order)
#     unique_references = []
#     seen = set()
#     for ref in all_references:
#         if ref not in seen:
#             seen.add(ref)
#             unique_references.append(ref)
    
#     return {
#         "method": "voting",
#         "individual_results": results,
#         "review_process": {
#             "individual_scores": scores,
#             "mean": round(mean_score, 2),
#             "std": round(np.std(scores), 2)
#         },
#         "consolidated_references": {  # ✅ New: consolidated references
#             "total_unique_references": len(unique_references),
#             "references_by_reviewer": reference_by_source,
#             "all_references": unique_references[:20]  # Display at most 20
#         },
#         "final_assessment": {
#             "consensus_score": consensus_score,
#             "confidence": round(max(0, 1 - np.std(scores)/5), 2),
#             "recommendation": recommendation,
#             "synthesis_rationale": f"Based on {len(scores)} reviews with mean {mean_score:.1f}"
#         }
#     }

# def get_nodes_by_label_with_fallback(G, label: str, label_field: str = 'type', case_sensitive: bool = False):
#     """
#     Enhanced get_nodes_by_label: supports type fallback
    
#     When searching for certain types, automatically tries related types if results are few:
#     - Cancer -> also searches Cancer and Disease
#     - Gene -> also searches Gene and Genetic
#     - Protein -> also searches Protein, Enzyme, Peptide
    
#     Args:
#         G: NetworkX graph object
#         label: Target label value (e.g., "Gene", "Cancer", "Protein")
#         label_field: Label field name (default 'type')
#         case_sensitive: Whether to match case-sensitively (default False)
    
#     Returns:
#         nodes_with_label: List of nodes matching the criteria (including fallback types)
#     """
    
#     # Define type fallback mapping
#     fallback_map = {
#         "Cancer": ["Cancer", "Disease"],  # Cancer type also includes Disease type cancers
#         "Gene": ["Gene", "Genetic"],      # Gene type also includes Genetic type
#         "Protein": ["Protein", "Enzyme", "Peptide"],  # Protein related types
#         "CellType": ["CellType", "Cell"],  # CellType also includes Cell
#         # Add more mappings as needed
#     }
    
#     # Determine list of types to search
#     if label in fallback_map:
#         types_to_search = fallback_map[label]
#     else:
#         types_to_search = [label]
    
#     # Collect all matching nodes
#     all_matching_nodes = []
    
#     for search_label in types_to_search:
#         # Execute search for each type
#         search_label_processed = search_label if case_sensitive else search_label.lower()
        
#         for node in G.nodes():
#             node_data = G.nodes[node]
#             node_label = node_data.get(label_field, '')
            
#             if isinstance(node_label, str):
#                 compare_label = node_label if case_sensitive else node_label.lower()
#                 if compare_label == search_label_processed:
#                     all_matching_nodes.append(node)
    
#     # Deduplicate (preserve order)
#     unique_nodes = []
#     seen = set()
#     for node in all_matching_nodes:
#         if node not in seen:
#             unique_nodes.append(node)
#             seen.add(node)
    
#     return unique_nodes

# def test_fallback(G):
#     """Test if fallback mechanism works correctly"""
    
#     print("🧪 Testing type fallback mechanism")
#     print("=" * 70)
    
#     # Test 1: Cancer (should include Disease)
#     print("\nTest 1: Find 'Cancer' type")
#     cancer_nodes = get_nodes_by_label(G, "Cancer")
#     cancer_fallback_nodes = get_nodes_by_label_with_fallback(G, "Cancer")
    
#     print(f"  Original function found: {len(cancer_nodes)} nodes")
#     print(f"  New function found: {len(cancer_fallback_nodes)} nodes")
#     print(f"  Added: {len(cancer_fallback_nodes) - len(cancer_nodes)} nodes")
    
#     # Show some examples
#     if len(cancer_fallback_nodes) > len(cancer_nodes):
#         print(f"\n  New node examples (first 10):")
#         new_nodes = [n for n in cancer_fallback_nodes if n not in cancer_nodes]
#         for i, node in enumerate(new_nodes[:10], 1):
#             node_type = G.nodes[node].get('type', 'NO_TYPE')
#             print(f"    {i}. {node} [type={node_type}]")
    
#     # Test 2: Gene (should include Genetic)
#     print("\nTest 2: Find 'Gene' type")
#     gene_nodes = get_nodes_by_label(G, "Gene")
#     gene_fallback_nodes = get_nodes_by_label_with_fallback(G, "Gene")
    
#     print(f"  Original function found: {len(gene_nodes)} nodes")
#     print(f"  New function found: {len(gene_fallback_nodes)} nodes")
#     print(f"  Added: {len(gene_fallback_nodes) - len(gene_nodes)} nodes")
    
#     # Test 3: Disease (no fallback, should be the same)
#     print("\nTest 3: Find 'Disease' type (no fallback)")
#     disease_nodes = get_nodes_by_label(G, "Disease")
#     disease_fallback_nodes = get_nodes_by_label_with_fallback(G, "Disease")
    
#     print(f"  Original function found: {len(disease_nodes)} nodes")
#     print(f"  New function found: {len(disease_fallback_nodes)} nodes")
#     print(f"  Added: {len(disease_fallback_nodes) - len(disease_nodes)} nodes")
    
#     print("\n" + "=" * 70)
#     print("✅ Test complete")

# # ==================== Europe PMC Search ====================
# def europepmc_search(query: str, max_results: int = 10) -> str:
#     """
#     Search Europe PMC (European PubMed Central)
    
#     Coverage: Biomedical literature, includes more European research
#     Advantage: Broader scope than PubMed, includes more open access content
    
#     Args:
#         query: Search query
#         max_results: Maximum number of results
    
#     Returns:
#         Formatted search results
#     """
#     try:
#         url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
#         params = {
#             "query": query,
#             "format": "json",
#             "pageSize": max_results,
#             "resultType": "core"
#         }
        
#         time.sleep(0.5)  # Courtesy delay
        
#         response = requests.get(url, params=params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         results_list = data.get("resultList", {}).get("result", [])
        
#         if not results_list:
#             return f"No results found in Europe PMC for query: {query}"
        
#         results = []
#         for article in results_list:
#             try:
#                 title = article.get("title", "No title")
#                 abstract = article.get("abstractText", "No abstract available")
#                 year = article.get("pubYear", "Unknown")
#                 pmid = article.get("pmid", "")
#                 pmcid = article.get("pmcid", "")
#                 doi = article.get("doi", "")
                
#                 # Build URL (prefer PMC, then PubMed, then DOI)
#                 if pmcid:
#                     url_link = f"https://europepmc.org/article/PMC/{pmcid.replace('PMC', '')}"
#                 elif pmid:
#                     url_link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
#                 elif doi:
#                     url_link = f"https://doi.org/{doi}"
#                 else:
#                     url_link = "No URL available"
                
#                 # Get source information
#                 source = article.get("journalTitle", article.get("source", "Unknown"))
                
#                 results.append({
#                     "title": title,
#                     "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
#                     "year": str(year),
#                     "source": source,
#                     "url": url_link,
#                     "pmid": pmid,
#                     "pmcid": pmcid,
#                     "doi": doi
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"Europe PMC Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Source: {paper['source']}\n"
#             output += f"   Year: {paper['year']}\n"
#             if paper['pmid']:
#                 output += f"   PMID: {paper['pmid']}\n"
#             if paper['pmcid']:
#                 output += f"   PMCID: {paper['pmcid']}\n"
#             if paper['doi']:
#                 output += f"   DOI: {paper['doi']}\n"
#             output += f"   URL: {paper['url']}\n"
#             if paper['abstract'] != "No abstract available":
#                 output += f"   Abstract: {paper['abstract']}\n"
#             output += "\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching Europe PMC: {str(e)}"

# # ==================== bioRxiv/medRxiv Search ====================
# def biorxiv_search(query: str, max_results: int = 10) -> str:
#     """
#     Search bioRxiv and medRxiv preprints
    
#     Coverage: Latest preprints in biology and medicine
#     Advantage: Access latest unpublished research
    
#     Args:
#         query: Search query
#         max_results: Maximum number of results
    
#     Returns:
#         Formatted search results
#     """
#     try:
#         # bioRxiv API does not support direct keyword search, requires date ranges
#         # Here we use a workaround: search recent papers, then filter
#         # Or use Europe PMC bioRxiv data
        
#         # Approach: Use Europe PMC Search bioRxiv/medRxiv
#         url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        
#         # Restrict to bioRxiv and medRxiv
#         enhanced_query = f"{query} AND (SRC:PPR OR SRC:MED)"
        
#         params = {
#             "query": enhanced_query,
#             "format": "json",
#             "pageSize": max_results,
#             "resultType": "core"
#         }
        
#         time.sleep(0.5)  # Courtesy delay
        
#         response = requests.get(url, params=params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         results_list = data.get("resultList", {}).get("result", [])
        
#         if not results_list:
#             return f"No results found in bioRxiv/medRxiv for query: {query}"
        
#         results = []
#         for article in results_list:
#             try:
#                 title = article.get("title", "No title")
#                 abstract = article.get("abstractText", "No abstract available")
#                 year = article.get("pubYear", "Unknown")
#                 doi = article.get("doi", "")
                
#                 # Determine source
#                 source = article.get("source", "")
#                 if "biorxiv" in source.lower():
#                     preprint_source = "bioRxiv"
#                 elif "medrxiv" in source.lower():
#                     preprint_source = "medRxiv"
#                 else:
#                     preprint_source = "Preprint"
                
#                 # Build URL
#                 if doi:
#                     if "biorxiv" in doi.lower():
#                         url_link = f"https://www.biorxiv.org/content/{doi}"
#                     elif "medrxiv" in doi.lower():
#                         url_link = f"https://www.medrxiv.org/content/{doi}"
#                     else:
#                         url_link = f"https://doi.org/{doi}"
#                 else:
#                     url_link = "No URL available"
                
#                 results.append({
#                     "title": title,
#                     "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
#                     "year": str(year),
#                     "source": preprint_source,
#                     "url": url_link,
#                     "doi": doi
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"bioRxiv/medRxiv Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             output += f"   Source: {paper['source']} (Preprint)\n"
#             output += f"   Year: {paper['year']}\n"
#             if paper['doi']:
#                 output += f"   DOI: {paper['doi']}\n"
#             output += f"   URL: {paper['url']}\n"
#             if paper['abstract'] != "No abstract available":
#                 output += f"   Abstract: {paper['abstract']}\n"
#             output += "\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching bioRxiv/medRxiv: {str(e)}"

# # ==================== CrossRef Search ====================
# def crossref_search(query: str, max_results: int = 10) -> str:
#     """
#     Search CrossRef academic database
    
#     Coverage: Cross-disciplinary academic literature, strong citation data
#     Advantage: Most comprehensive DOI database, excellent metadata quality
    
#     Args:
#         query: Search query
#         max_results: Maximum number of results
    
#     Returns:
#         Formatted search results
#     """
#     try:
#         url = "https://api.crossref.org/works"
        
#         # Get email from env var (optional, but recommended for better rate limits)
#         email = os.environ.get("CROSSREF_EMAIL", "your-email@example.com")
        
#         params = {
#             "query": query,
#             "rows": max_results,
#             "mailto": email,
#             "select": "title,author,published-print,DOI,abstract,container-title,type"
#         }
        
#         time.sleep(0.5)  # Courtesy delay
        
#         response = requests.get(url, params=params, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         items = data.get("message", {}).get("items", [])
        
#         if not items:
#             return f"No results found in CrossRef for query: {query}"
        
#         results = []
#         for item in items:
#             try:
#                 # Extract title
#                 title_list = item.get("title", [])
#                 title = title_list[0] if title_list else "No title"
                
#                 # Extract abstract (if available)
#                 abstract = item.get("abstract", "No abstract available")
                
#                 # Extract year
#                 published = item.get("published-print", item.get("published-online", {}))
#                 date_parts = published.get("date-parts", [[]])
#                 year = str(date_parts[0][0]) if date_parts and date_parts[0] else "Unknown"
                
#                 # Extract DOI
#                 doi = item.get("DOI", "")
#                 url_link = f"https://doi.org/{doi}" if doi else "No URL available"
                
#                 # Extract source (journal name)
#                 container_title = item.get("container-title", [])
#                 source = container_title[0] if container_title else "Unknown"
                
#                 # Extract authors
#                 authors = item.get("author", [])
#                 author_names = []
#                 for author in authors[:3]:
#                     given = author.get("given", "")
#                     family = author.get("family", "")
#                     if given and family:
#                         author_names.append(f"{given} {family}")
#                     elif family:
#                         author_names.append(family)
                
#                 author_str = ", ".join(author_names)
#                 if len(authors) > 3:
#                     author_str += " et al."
                
#                 # Extract publication type
#                 pub_type = item.get("type", "unknown")
                
#                 results.append({
#                     "title": title,
#                     "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
#                     "year": year,
#                     "source": source,
#                     "authors": author_str,
#                     "url": url_link,
#                     "doi": doi,
#                     "type": pub_type
#                 })
#             except Exception as e:
#                 continue
        
#         # Format output
#         output = f"CrossRef Search Results for: {query}\n"
#         output += "=" * 70 + "\n\n"
        
#         for i, paper in enumerate(results, 1):
#             output += f"{i}. {paper['title']}\n"
#             if paper['authors']:
#                 output += f"   Authors: {paper['authors']}\n"
#             output += f"   Source: {paper['source']}\n"
#             output += f"   Year: {paper['year']}\n"
#             output += f"   Type: {paper['type']}\n"
#             if paper['doi']:
#                 output += f"   DOI: {paper['doi']}\n"
#             output += f"   URL: {paper['url']}\n"
#             if paper['abstract'] != "No abstract available":
#                 output += f"   Abstract: {paper['abstract']}\n"
#             output += "\n"
        
#         return output
        
#     except Exception as e:
#         return f"Error searching CrossRef: {str(e)}"




"""
Knowledge Graph Reasoning and Literature Search Utilities

This module provides tools for:
1. Knowledge graph path finding and reasoning
2. Multi-source academic literature search
3. LLM-based node validation
4. Biomedical entity relationship analysis

Author: Modified and organized version
"""

# ==================== IMPORTS ====================
# Standard Library
import functools
import json
import os
import random
import re
import time
from typing import Annotated, Dict, List, Optional, Tuple, Union
from xml.etree import ElementTree as ET

# Third-party Libraries
import requests
import seaborn as sns
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Local Imports
from GraphReasoning import *

# Jupyter Notebook Detection
try:
    get_ipython()
except NameError:
    pass

# Seaborn Configuration
sns.set_palette("hls")


# ==================== CONFIGURATION ====================
# API Keys
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NCBI_API_KEY = os.getenv("NCBI_API_KEY")

# Allowed Node Types for Knowledge Graph
_ALLOWED_TYPES = {
    # Molecular Biology
    "Gene", "Protein", "Enzyme", "Peptide", "AminoAcid",
    "DNA", "RNA", "LncRNA", "RNAi", "Transcript",
    "Nucleic", "NucleicAcid", "Molecule", "Antibody", "Antigen",
    
    # Cellular
    "Cell", "CellType", "CellLine", "CellCluster", "StemCell",
    "Neuron", "Exosome", "Nucleus",
    
    # Anatomical
    "Organ", "Tissue", "AnatomicalStructure", "Anatomy",
    "Brain", "Bone", "Muscle", "Nerve", "Artery", "Vein",
    "Vessel", "Lymph", "LymphNode", "Gland", "Duct",
    "Urethra", "Vertebra", "SpinalSegment",
    
    # Disease & Pathology
    "Disease", "Cancer", "CancerType", "Tumor", "Syndrome",
    "Symptom", "Condition", "Complication", "Injury", "Anomaly",
    "Metastasis", "Loss",
    
    # Pharmacology
    "Drug", "Compound", "Chemical", "Treatment", "Therapy",
    "Vaccine", "Nanoparticle", "Ion",
    
    # Biochemistry
    "Pathway", "Process", "Metabolite", "Cytokine",
    "Hormone", "Neurotransmitter", "Biomarker", "Marker",
    "Receptor", "Signal",
    
    # Genetics
    "Genetic", "Genome", "Genomic", "Genotype", "Variant",
    "Mutation", "Chromosome", "Chromosomal", "Karyotype",
    "Plasmid", "Vector",
    
    # Clinical
    "Patient", "PatientGroup", "Cohort", "ClinicalTrial",
    "ClinicalFactor", "ClinicalVariable", "Outcome",
    "MedicalProcedure", "Surgery", "SurgicalProcedure",
    "MedicalProfessional", "Surgeon", "Specialist",
    
    # Research & Methods
    "Method", "Technique", "Assay", "Protocol", "Procedure",
    "Measurement", "Test", "Experiment", "Study",
    "ImagingTechnique", "Imaging",
    
    # Data & Informatics
    "Data", "Dataset", "Database", "Model", "Algorithm",
    "Network", "Graph", "Classification", "Ontology",
    "Reference", "Publication", "Atlas",
    
    # Organisms
    "Organism", "Species", "Genus", "Phylum",
    "Bacteria", "Bacterium", "Virus", "Parasite",
    "Microorganism", "Microbiota", "Microbiome",
    "Mouse", "Embryo", "Plant", "Cultivar",
    
    # Demographics & Social
    "Age", "Gender", "Sex", "Race", "Ethnicity",
    "Socioeconomic", "SocioeconomicStatus", "Population",
    "Demographic",
    
    # Medical Factors
    "RiskFactor", "Risk", "RiskScore", "RiskCategory",
    "Phenotype", "Trait", "Characteristic", "Feature",
    "Grade", "Stage", "Subtype", "Type",
    
    # Equipment & Materials
    "Device", "Catheter", "Stent", "Sling", "Robot",
    "Laser", "Hardware", "Software", "Tool",
    "Material", "Matrix", "Particle",
    
    # Location & Organization
    "Location", "Region", "Space", "Layer", "Section",
    "Hospital", "Facility", "Laboratory", "Organization",
    
    # Other
    "Diet", "Food", "Fluid", "Channel", "Fiber",
    "Bundle", "Complex", "Component", "Target",
    "Function", "Effect", "Phenomenon", "Evidence",
    "Involvement", "Representation", "Modification",
    "Prevalence", "Cost", "Service", "Insurance",
    "Questionnaire", "Criteria", "Definition",
    "Information", "Image", "Specimen", "Sample",
    "Case", "Field", "Domain", "Task", "Object",
    "Factor", "Parameter", "Metric", "Result",
    "Percentage", "Size", "Temperature", "Dosage",
    "TimePoint", "TimePeriod", "Setting", "Modality",
}

# Disease Substring Filters
_ALLOWED_DISEASE_SUBSTR = ("bladder cancer", "blca", "tcga-blca")

# Category Words (for validation)
CATEGORY_WORDS = {
    "acid", "age", "algorithm", "amino", "aminoacid", "anatomical",
    "anatomicalstructure", "anatomy", "anomaly", "antibody", "antigen",
    "artery", "assay", "atlas", "bacteria", "bacterium", "benchmark",
    "biomarker", "bone", "brain", "bundle", "cancer", "cancertype",
    "case", "catheter", "cell", "cellcluster", "cellline", "celltype",
    "channel", "characteristic", "chemical", "chromosomal", "chromosome",
    "classification", "clinical", "clinicalfactor", "clinicaltrial",
    "clinicalvariable", "cluster", "cohort", "combination", "complex",
    "complication", "component", "composite", "compound", "condition",
    "cost", "criteria", "cultivar", "cytokine", "dna", "data", "database",
    "dataset", "definition", "demographic", "device", "diet", "disease",
    "domain", "dosage", "drug", "duct", "effect", "embryo", "enzyme",
    "ethnicity", "evidence", "exosome", "experiment", "facility", "factor",
    "feature", "fiber", "field", "fluid", "food", "formula", "formulation",
    "function", "gender", "gene", "genetic", "genome", "genomic", "genotype",
    "genus", "gland", "grade", "graph", "ground", "group", "groups",
    "hardware", "hormone", "hospital", "image", "imaging", "imagingtechnique",
    "information", "injury", "insurance", "involvement", "ion", "karyotype",
    "laboratory", "laser", "layer", "lineage", "lncrna", "location", "loss",
    "lymph", "lymphnode", "marker", "material", "matrix", "measurement",
    "medical", "medicalprocedure", "medicalprofessional", "metabolite",
    "metastasis", "method", "metric", "microbiome", "microbiota",
    "microorganism", "modality", "model", "modification", "molecule",
    "mouse", "muscle", "mutation", "n/a", "nanoparticle", "nerve",
    "network", "neuron", "neurotransmitter", "node", "nucleic", "nucleicacid",
    "nucleus", "object", "ontology", "organ", "organism", "organization",
    "outcome", "parameter", "parasite", "particle", "pathway", "patient",
    "patientgroup", "peptide", "percentage", "phenomenon", "phenotype",
    "phylum", "plant", "plasmid", "population", "prevalence", "procedure",
    "process", "product", "profession", "protein", "protocol", "publication",
    "questionnaire", "rna", "rnai", "race", "receptor", "reference",
    "region", "representation", "research", "result", "risk", "riskcategory",
    "riskfactor", "riskscore", "river", "robot", "relation", "sample",
    "section", "service", "setting", "sex", "signal", "size", "sling",
    "socioeconomic", "socioeconomicstatus", "software", "space", "specialist",
    "specialty", "species", "specimen", "spinalsegment", "stage", "statistical",
    "stemcell", "stent", "structure", "study", "subtype", "surgeon", "surgery",
    "surgical", "surgicalprocedure", "symptom", "syndrome", "system", "target",
    "task", "technique", "temperature", "test", "therapy", "timeperiod",
    "timepoint", "tissue", "token", "tool", "trait", "transcript", "transform",
    "treatment", "truth", "tumor", "urethra", "vaccine", "variant", "vector",
    "vein", "vertebra", "vessel", "virus", "virus-like",
}

# Invalid Labels
INVALID_LABELS = {"knowledge path", "research", "idea", "concept"}

# Type Fallback Mapping
FALLBACK_MAP = {
    "Cancer": ["Cancer", "Disease"],
    "Gene": ["Gene", "Genetic"],
    "Protein": ["Protein", "Enzyme", "Peptide"],
    "CellType": ["CellType", "Cell"],
}


# ==================== UTILITY FUNCTIONS ====================
def _node_type(nid: str) -> str:
    """Extract the type part from a 'name||Type' formatted node id."""
    return str(nid).split("||", 1)[1] if "||" in str(nid) else ""


def _node_name(nid: str) -> str:
    """Extract the name part from a 'name||Type' formatted node id."""
    return str(nid).split("||", 1)[0]


def _keep_node(nid: str) -> bool:
    """Filter nodes: keep only allowed types and disease substrings."""
    t = _node_type(nid)
    if t not in _ALLOWED_TYPES:
        return False
    if t == "Disease":
        nm = _node_name(nid).lower()
        return any(k in nm for k in _ALLOWED_DISEASE_SUBSTR)
    return True


def clean_path_string(path_str: str) -> str:
    """
    Clean noisy path strings of the form: 'A||Type -- rel -- B||Type -- ...'
    
    - Keep only nodes that pass _keep_node
    - Preserve relations only between valid nodes
    - Deduplicate consecutive nodes
    - If everything is removed, return original string
    """
    if not path_str:
        return path_str

    toks = [t.strip() for t in path_str.split("--")]
    if not toks:
        return path_str

    cleaned = []
    i = 0
    while i < len(toks):
        token = toks[i]
        if "||" in token:  # Node
            if _keep_node(token):
                if not cleaned or cleaned[-1] != token:
                    cleaned.append(token)
                if i + 2 < len(toks):
                    rel = toks[i + 1]
                    nxt = toks[i + 2]
                    if "||" in nxt and _keep_node(nxt):
                        cleaned.append(rel)
                        cleaned.append(nxt)
                        i += 2
        i += 1

    # Deduplicate consecutive nodes
    dedup = []
    for t in cleaned:
        if not dedup or dedup[-1] != t:
            dedup.append(t)

    # Fallback if nothing valid left
    if len(dedup) < 3:
        return path_str

    return " -- ".join(dedup)


def extract_keywords(sentence: str, max_k: int = 3) -> List[str]:
    """
    Extract keyword candidates from a sentence.
    
    - Split by non-alphanumeric
    - Filter stop words
    - Deduplicate
    - Sort by length
    """
    toks = re.findall(r"[A-Za-z0-9+\-_/]+", sentence.lower())
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", 
            "with", "between", "from", "into", "as", "by"}
    cand = [t for t in toks if t not in stop and len(t) > 2]

    uniq, seen = [], set()
    for t in cand:
        if t not in seen:
            uniq.append(t)
            seen.add(t)

    uniq.sort(key=len, reverse=True)
    return uniq[:max_k]


def instrument(tool_name: str, fn, tool_events: list):
    """Wrap a tool function to log execution time, args, and results into tool_events."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            elapsed = int((time.time() - t0) * 1000)
            preview = (str(result)[:500] + "…") if isinstance(result, str) and len(result) > 500 else str(result)
            print(f"[TOOL] {tool_name} called in {elapsed} ms")
            tool_events.append({
                "tool": tool_name, 
                "elapsed_ms": elapsed, 
                "args": {"args": list(args), "kwargs": kwargs}, 
                "result_preview": preview
            })
            return result
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            print(f"[TOOL] {tool_name} FAILED in {elapsed} ms: {e}")
            tool_events.append({
                "tool": tool_name, 
                "elapsed_ms": elapsed, 
                "args": {"args": list(args), "kwargs": kwargs}, 
                "error": repr(e)
            })
            raise
    return wrapped


# ==================== GRAPH NODE OPERATIONS ====================
def get_nodes_by_label(
    G, 
    label: str, 
    label_field: str = 'type', 
    case_sensitive: bool = False
) -> List[str]:
    """
    Get all nodes in the graph by label.
    
    Args:
        G: NetworkX graph object
        label: Target label value (e.g., "Gene", "Disease", "Protein")
        label_field: Label field name (default 'type')
        case_sensitive: Whether to match case-sensitively (default False)
    
    Returns:
        nodes_with_label: List of nodes matching the criteria
    """
    nodes_with_label = []
    search_label = label if case_sensitive else label.lower()
    
    for node in G.nodes():
        node_data = G.nodes[node]
        node_label = node_data.get(label_field, '')
        
        if isinstance(node_label, str):
            compare_label = node_label if case_sensitive else node_label.lower()
            if compare_label == search_label:
                nodes_with_label.append(node)
    
    return nodes_with_label


def get_nodes_by_label_with_fallback(
    G, 
    label: str, 
    label_field: str = 'type', 
    case_sensitive: bool = False
) -> List[str]:
    """
    Enhanced version with type fallback support.
    
    When searching for certain types, automatically tries related types if results are sparse:
    - Cancer → searches both Cancer and Disease
    - Gene → searches both Gene and Genetic
    - Protein → searches Protein, Enzyme, Peptide
    
    Args:
        G: NetworkX graph object
        label: Target label value
        label_field: Label field name (default 'type')
        case_sensitive: Whether to match case-sensitively (default False)
    
    Returns:
        Unique list of nodes matching the criteria (including fallback types)
    """
    types_to_search = FALLBACK_MAP.get(label, [label])
    
    all_matching_nodes = []
    for search_label in types_to_search:
        search_label_processed = search_label if case_sensitive else search_label.lower()
        
        for node in G.nodes():
            node_data = G.nodes[node]
            node_label = node_data.get(label_field, '')
            
            if isinstance(node_label, str):
                compare_label = node_label if case_sensitive else node_label.lower()
                if compare_label == search_label_processed:
                    all_matching_nodes.append(node)
    
    # Deduplicate while preserving order
    unique_nodes = []
    seen = set()
    for node in all_matching_nodes:
        if node not in seen:
            unique_nodes.append(node)
            seen.add(node)
    
    return unique_nodes


def extract_target_type_nodes_from_path(
    path_list: List[str], 
    G, 
    target_label: str, 
    label_field: str = 'type'
) -> List[str]:
    """
    Extract nodes of a specific type from a path.
    
    Args:
        path_list: List of nodes in the path
        G: Graph object
        target_label: Target node type
        label_field: Label field name
    
    Returns:
        List of nodes of that type
    """
    target_nodes = []
    for node in path_list:
        if node and node != "":
            node_data = G.nodes.get(node, {})
            node_type = node_data.get(label_field, "")
            if node_type.lower() == target_label.lower():
                target_nodes.append(node)
    return target_nodes


# ==================== PATH GENERATION ====================
def create_path(
    G,
    embedding_tokenizer,
    embedding_model,
    node_embeddings,
    generate_graph_expansion=None,
    second_hop=False,
    data_dir="./",
    save_files=False,
    verbatim=False,
    keyword_1=None,
    keyword_2=None,
    shortest_path=True,
    top_k=5,
    randomness_factor=0,
    num_random_waypoints=0,
):
    """Create a path between two keywords, optionally shortest-path or randomized."""
    if keyword_1 is None or keyword_2 is None:
        random_nodes = random.sample(list(G.nodes()), 2)
        keyword_1 = keyword_1 or random_nodes[0]
        keyword_2 = keyword_2 or random_nodes[1]

    if shortest_path:
        (
            (best_node_1, best_similarity_1, best_node_2, best_similarity_2),
            path,
            path_graph,
            shortest_path_length,
            fname,
            graph_GraphML,
        ) = find_path(
            G,
            node_embeddings,
            embedding_tokenizer,
            embedding_model,
            second_hop=False,
            data_dir=data_dir,
            save_files=False,
            keyword_1=keyword_1,
            keyword_2=keyword_2,
        )
    else:
        if randomness_factor > 0 or num_random_waypoints > 0:
            path, path_graph, shortest_path_length, _, _ = (
                heuristic_path_with_embeddings_with_randomization_waypoints(
                    G,
                    embedding_tokenizer,
                    embedding_model,
                    keyword_1,
                    keyword_2,
                    node_embeddings,
                    top_k=5,
                    second_hop=False,
                    data_dir=data_dir,
                    verbatim=verbatim,
                    save_files=False,
                    randomness_factor=randomness_factor,
                    num_random_waypoints=num_random_waypoints,
                )
            )
        else:
            path, path_graph, shortest_path_length, _, _ = heuristic_path_with_embeddings(
                G,
                embedding_tokenizer,
                embedding_model,
                keyword_1,
                keyword_2,
                node_embeddings,
                top_k=top_k,
                second_hop=False,
                data_dir=data_dir,
                verbatim=verbatim,
                save_files=save_files,
            )

    path_list_for_vis, path_list_for_vis_string = print_path_with_edges_as_list(
        G, path, keywords_separator=" -- "
    )

    return path_list_for_vis, path_list_for_vis_string


def create_multi_keyword_path(
    keywords, 
    G, 
    embedding_tokenizer, 
    embedding_model, 
    node_embeddings
):
    """Create continuous paths through multiple keywords."""
    all_paths = []
    all_path_strings = []
    combined_path_list = []

    for i in range(len(keywords) - 1):
        keyword_1 = keywords[i]
        keyword_2 = keywords[i + 1]

        try:
            path_list_for_vis, path_list_for_vis_string = create_path(
                G,
                embedding_tokenizer,
                embedding_model,
                node_embeddings,
                generate_graph_expansion=None,
                randomness_factor=0.2,
                num_random_waypoints=4,
                shortest_path=True,
                second_hop=False,
                data_dir="./",
                save_files=False,
                verbatim=False,
                keyword_1=keyword_1,
                keyword_2=keyword_2,
            )

            all_paths.append(path_list_for_vis)
            all_path_strings.append(path_list_for_vis_string)

            if i == 0:
                combined_path_list.extend(path_list_for_vis)
            else:
                if len(path_list_for_vis) > 1:
                    combined_path_list.extend(path_list_for_vis[1:])
                else:
                    combined_path_list.extend(path_list_for_vis)

        except Exception as e:
            print(f"Path {i + 1} ({keyword_1} → {keyword_2}) failed: {str(e)}")
            if i == 0:
                combined_path_list.extend([keyword_1, "", keyword_2])
            else:
                combined_path_list.extend(["", keyword_2])

    combined_path_string = " -- ".join(str(item) for item in combined_path_list if item != "")
    individual_segments = "\n\n".join(
        f"Segment {i + 1}: {path_str}" for i, path_str in enumerate(all_path_strings)
    )

    return combined_path_list, combined_path_string


def generate_path(
    G,
    embedding_tokenizer,
    embedding_model,
    node_embeddings,
    keyword_1: Annotated[str, "First keyword"],
    keyword_2: Annotated[str, "Second keyword"],
    keyword_3: Annotated[Union[str, None], "Third keyword (optional)"] = None,
    keyword_4: Annotated[Union[str, None], "Fourth keyword (optional)"] = None,
    keyword_5: Annotated[Union[str, None], "Fifth keyword (optional)"] = None,
) -> str:
    """Generate a path string between 2–5 keywords using helper functions."""
    keyword_list = [keyword_1, keyword_2]
    for kw in [keyword_3, keyword_4, keyword_5]:
        if kw is not None and str(kw).strip():
            keyword_list.append(str(kw).strip())

    if len(keyword_list) == 2:
        path_list_for_vis, path_list_for_vis_string = create_path(
            G,
            embedding_tokenizer,
            embedding_model,
            node_embeddings,
            generate_graph_expansion=None,
            randomness_factor=0.2,
            num_random_waypoints=4,
            shortest_path=True,
            second_hop=False,
            data_dir="./",
            save_files=False,
            verbatim=False,
            keyword_1=keyword_list[0],
            keyword_2=keyword_list[1],
        )
        return path_list_for_vis_string
    else:
        combined_path_list, combined_path_string = create_multi_keyword_path(
            keyword_list, G, embedding_tokenizer, embedding_model, node_embeddings
        )
        return combined_path_string


def generate_path_from_sentence(
    G, 
    embedding_tokenizer, 
    embedding_model, 
    node_embeddings, 
    sentence: str
) -> str:
    """Extract keywords from a sentence and generate a path connecting them."""
    kws = extract_keywords(sentence, max_k=5)
    if len(kws) < 2:
        raise ValueError(f"Not enough keywords extracted from: {sentence!r}")

    return generate_path(
        G, embedding_tokenizer, embedding_model, node_embeddings,
        keyword_1=kws[0], 
        keyword_2=kws[1],
        keyword_3=(kws[2] if len(kws) > 2 else None),
        keyword_4=(kws[3] if len(kws) > 3 else None),
        keyword_5=(kws[4] if len(kws) > 4 else None),
    )


# ==================== LLM VALIDATION ====================
def call_openai(client, model: str, prompt: str) -> str:
    """Call OpenAI API for validation."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a biomedical knowledge expert specializing in validating entity names. Always respond with valid JSON only."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0,
        max_tokens=4096,
        response_format={"type": "json_object"}
    )
    return response.choices[0].message.content


def call_anthropic(client, model: str, prompt: str) -> str:
    """Call Anthropic API for validation."""
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[{
            "role": "user",
            "content": prompt
        }]
    )
    return response.content[0].text


def create_path_validation_prompt(
    node_names: List[str], 
    node_type: str, 
    path_string: str
) -> str:
    """Create prompt for path validation."""
    type_criteria = {
        "Gene": {
            "valid_examples": ["BRCA1", "TP53", "EGFR", "IL6", "VEGFA"],
            "invalid_examples": [
                "neuroinflammatory genes",
                "genes and synaptic biology",
                "high ENO1 expression",
                "gene collection"
            ],
        },
        "Protein": {
            "valid_examples": ["p53 protein", "insulin", "hemoglobin"],
            "invalid_examples": ["proteins in the cell", "protein collection"],
        },
        "Disease": {
            "valid_examples": ["breast cancer", "Alzheimer's disease", "type 2 diabetes"],
            "invalid_examples": ["diseases of the heart", "disease collection"],
        },
        "Drug": {
            "valid_examples": ["aspirin", "metformin", "cisplatin"],
            "invalid_examples": ["cancer drugs", "drug collection"],
        }
    }
    
    criteria = type_criteria.get(node_type, {
        "valid_examples": ["specific entity"],
        "invalid_examples": ["generic description"],
    })
    
    prompt = f"""You are validating nodes in a knowledge graph path. The path is:

{path_string}

Focus on validating these {node_type} nodes:
{chr(10).join(f'{i+1}. "{name}"' for i, name in enumerate(node_names))}

VALIDATION CRITERIA:
- VALID {node_type} examples: {', '.join(criteria['valid_examples'])}
- INVALID examples (reject these): {', '.join(criteria['invalid_examples'])}

RULES:
1. Node names with "genes", "proteins", "collection", "expression", "associated" → INVALID
2. Phrases with "and", "or", numbers like "1159 sets" → INVALID
3. Modifiers like "high", "low", "increased" → INVALID
4. Real {node_type} names are concise and specific

For each node, determine if it's a VALID {node_type} or INVALID.

OUTPUT FORMAT (JSON only):
{{
    "node_validations": [
        {{"name": "node1", "valid": true, "reason": "Valid gene symbol"}},
        {{"name": "node2", "valid": false, "reason": "Descriptive phrase, not specific gene"}},
        ...
    ]
}}

Return ONLY valid JSON."""

    return prompt


def parse_path_validation_response(
    response_text: str, 
    node_names: List[str]
) -> Dict[str, Dict]:
    """
    Parse path validation response.
    
    Returns:
        Dict mapping node_name -> {"valid": bool, "reason": str}
    """
    try:
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        result = json.loads(response_text)
        validations = result.get("node_validations", [])
        
        node_results = {}
        for item in validations:
            node_name = item.get("name")
            node_results[node_name] = {
                "valid": item.get("valid", True),
                "reason": item.get("reason", "No reason provided")
            }
        
        # Default to valid for nodes without results
        for node in node_names:
            if node not in node_results:
                node_results[node] = {"valid": True, "reason": "Not evaluated"}
        
        return node_results
    
    except Exception as e:
        print(f"⚠️ Warning: Could not parse validation response: {e}")
        return {node: {"valid": True, "reason": "Parse error"} for node in node_names}


def validate_path_nodes_with_llm(
    path_string: str,
    node_names: List[str],
    node_type: str,
    llm_client,
    model: str = "gpt-4o-mini",
    client_type: str = "openai",
    verbatim: bool = False
) -> Tuple[bool, str, Dict[str, str]]:
    """
    Validate nodes in a single path.
    
    Args:
        path_string: Complete path string
        node_names: List of node names to validate
        node_type: Node type (e.g., "Gene")
        llm_client: LLM client
        model: Model name
        client_type: 'openai' or 'anthropic'
        verbatim: Whether to output detailed information
    
    Returns:
        is_valid: Whether path is valid (all critical nodes are valid)
        reason: Explanation if invalid
        node_validations: Validation results for each node
    """
    if not node_names:
        return True, "No nodes to validate", {}
    
    try:
        prompt = create_path_validation_prompt(node_names, node_type, path_string)
        
        if client_type == "openai":
            response_text = call_openai(llm_client, model, prompt)
        else:
            response_text = call_anthropic(llm_client, model, prompt)
        
        node_validations = parse_path_validation_response(response_text, node_names)
        
        invalid_nodes = [node for node, result in node_validations.items() 
                        if not result.get("valid", False)]
        
        if invalid_nodes:
            is_valid = False
            reasons = [f"{node}: {node_validations[node].get('reason', 'Unknown')}" 
                      for node in invalid_nodes]
            reason = f"Invalid nodes found: {'; '.join(reasons)}"
        else:
            is_valid = True
            reason = "All nodes validated successfully"
        
        return is_valid, reason, node_validations
    
    except Exception as e:
        return True, f"Validation error: {str(e)}", {}


# ==================== PATH FINDING TO LABEL ====================
def find_paths_to_label(
    G,
    embedding_tokenizer,
    embedding_model,
    node_embeddings,
    source_keyword: str,
    target_label: str,
    label_field: str = 'type',
    max_targets: Optional[int] = 20,
    shortest_path: bool = True,
    top_k: int = 5,
    randomness_factor: float = 0,
    num_random_waypoints: int = 0,
    verbatim: bool = False,
    data_dir: str = "./",
    save_files: bool = False,
    validate_nodes: bool = True,
    use_llm_validation: bool = False,
    llm_client=None,
    llm_model: str = "gpt-4o-mini",
    client_type: str = "openai",
):
    """
    Find paths from a single keyword to all nodes with a specified label.
    
    NEW: Validates nodes AFTER finding paths for efficiency.
    """
    # 1. Get all target nodes
    all_target_nodes = get_nodes_by_label_with_fallback(
        G, target_label, label_field=label_field
    )
    
    if not all_target_nodes:
        return {}, {
            "error": f"No nodes found with label '{target_label}' in field '{label_field}'",
            "total_targets": 0,
            "successful": 0,
            "failed": 0
        }
    
    if verbatim:
        print(f"\n{'='*60}")
        print(f"📋 PATH FINDING (validation will be done AFTER path finding)")
        print(f"{'='*60}")
        print(f"Found {len(all_target_nodes)} nodes of type '{target_label}'")
        print(f"{'='*60}\n")
    
    # 2. Limit number if needed
    target_nodes = all_target_nodes
    if max_targets and len(target_nodes) > max_targets:
        target_nodes = random.sample(target_nodes, max_targets)
        if verbatim:
            print(f"Limited to {max_targets} nodes for path calculation")
    
    # 3. Find all paths first (no validation)
    paths_dict = {}
    failed_targets = []
    
    if verbatim:
        print(f"\n🔍 Step 1: Finding paths to {len(target_nodes)} target nodes...")
    
    for i, target_node in enumerate(target_nodes, 1):
        if verbatim and i % 10 == 0:
            print(f"   Progress: {i}/{len(target_nodes)}")
        
        try:
            path_list, path_string = create_path(
                G,
                embedding_tokenizer,
                embedding_model,
                node_embeddings,
                keyword_1=source_keyword,
                keyword_2=target_node,
                shortest_path=shortest_path,
                top_k=top_k,
                randomness_factor=randomness_factor,
                num_random_waypoints=num_random_waypoints,
                verbatim=False,
                data_dir=data_dir,
                save_files=save_files
            )
            
            path_length = len([x for x in path_list if x != ""])
            paths_dict[target_node] = {
                "path_list": path_list,
                "path_string": path_string,
                "path_length": path_length,
                "validated": False,
                "validation_result": None
            }
            
        except Exception as e:
            failed_targets.append((target_node, str(e)))
            if verbatim:
                print(f"   ❌ Path to {target_node} failed: {e}")
    
    if verbatim:
        print(f"\n✅ Found {len(paths_dict)} paths")
    
    # 4. Validate shortest paths only
    if validate_nodes and use_llm_validation and llm_client and paths_dict:
        if verbatim:
            print(f"\n🤖 Step 2: Validating nodes in shortest paths using {llm_model}...\n")
        
        sorted_paths = sorted(paths_dict.items(), key=lambda x: x[1]['path_length'])
        num_paths_to_validate = min(30, len(sorted_paths))
        validated_count = 0
        invalid_count = 0
        
        for i, (target_node, path_info) in enumerate(sorted_paths[:num_paths_to_validate], 1):
            if verbatim:
                print(f"\rValidating: {i}/{num_paths_to_validate} | ✅ Valid: {validated_count} | ❌ Invalid: {invalid_count}", 
                      end='', flush=True)
            
            target_type_nodes = extract_target_type_nodes_from_path(
                path_info['path_list'], G, target_label, label_field
            )
            
            if not target_type_nodes:
                paths_dict[target_node]['validated'] = True
                paths_dict[target_node]['validation_result'] = {
                    "valid": True,
                    "reason": "No target type nodes to validate"
                }
                validated_count += 1
                continue
            
            is_valid, reason, node_validations = validate_path_nodes_with_llm(
                path_string=path_info['path_string'],
                node_names=target_type_nodes,
                node_type=target_label,
                llm_client=llm_client,
                model=llm_model,
                client_type=client_type,
                verbatim=False
            )
            
            paths_dict[target_node]['validated'] = True
            paths_dict[target_node]['validation_result'] = {
                "valid": is_valid,
                "reason": reason,
                "node_validations": node_validations
            }
            
            if is_valid:
                validated_count += 1
            else:
                invalid_count += 1
        
        if verbatim:
            print()
            print(f"\n{'='*60}")
            print(f"VALIDATION COMPLETE")
            print(f"{'='*60}")
            print(f"Validated: {num_paths_to_validate} paths")
            print(f"✅ Valid: {validated_count}")
            print(f"❌ Invalid: {invalid_count}")
            print(f"⏭️  Not validated: {len(sorted_paths) - num_paths_to_validate} (lower priority paths)")
            print(f"{'='*60}")
    
    # 5. Return results and statistics
    summary = {
        "validation_method": "post-path-llm" if (validate_nodes and use_llm_validation) else "none",
        "total_found": len(all_target_nodes),
        "paths_calculated": len(target_nodes),
        "successful_paths": len(paths_dict),
        "failed_paths": len(failed_targets),
        "validated_paths": sum(1 for p in paths_dict.values() if p.get('validated', False)),
        "valid_paths": sum(1 for p in paths_dict.values() 
                          if p.get('validated', False) and p.get('validation_result', {}).get('valid', False)),
        "invalid_paths": sum(1 for p in paths_dict.values() 
                            if p.get('validated', False) and not p.get('validation_result', {}).get('valid', False)),
        "failed_details": failed_targets[:10],
        "validation_enabled": validate_nodes and use_llm_validation,
    }
    
    if verbatim:
        print(f"\n{'='*60}")
        print(f"✅ PATH FINDING AND VALIDATION COMPLETE")
        print(f"{'='*60}")
        print(f"Total target nodes found: {summary['total_found']}")
        print(f"Successful paths: {summary['successful_paths']}/{summary['paths_calculated']}")
        if summary['validation_enabled']:
            print(f"Validated paths: {summary['validated_paths']}")
            print(f"  - Valid: {summary['valid_paths']}")
            print(f"  - Invalid: {summary['invalid_paths']}")
        print(f"{'='*60}")
    
    return paths_dict, summary


def find_shortest_paths_to_label_for_agent(
    G,
    embedding_tokenizer,
    embedding_model,
    node_embeddings,
    source_keyword: str,
    target_label: str,
    max_results: int = 5,
    label_field: str = "type",
    validate_nodes: bool = True,
    use_llm_validation: bool = True,
    llm_client=None,
    llm_model: str = "gpt-4o-mini",
    client_type: str = "openai",
) -> str:
    """
    Find shortest paths from a SPECIFIC entity to ALL entities of a certain TYPE.
    
    This is an agent-friendly wrapper that returns formatted string results.
    """
    try:
        # Validate inputs
        if source_keyword.lower().strip() in CATEGORY_WORDS:
            return (
                f"❌ ERROR: '{source_keyword}' is a CATEGORY, not a specific entity.\n\n"
                f"Please use a SPECIFIC entity name as source_keyword."
            )
        
        if target_label.lower() in INVALID_LABELS:
            return (
                f"❌ ERROR: '{target_label}' is not a valid node type in the graph.\n\n"
                f"Valid node types are: Gene, Disease, Protein, Drug, Compound, CellType, Process, Metabolite"
            )
        
        if use_llm_validation and llm_client is None:
            print("⚠️ Warning: LLM validation requested but no llm_client provided.")
            print("   Falling back to no validation.")
            use_llm_validation = False
        
        # Find paths
        paths_dict, summary = find_paths_to_label(
            G,
            embedding_tokenizer,
            embedding_model,
            node_embeddings,
            source_keyword=source_keyword,
            target_label=target_label,
            label_field=label_field,
            max_targets=max_results * 2,
            shortest_path=True,
            verbatim=True,
            validate_nodes=validate_nodes,
            use_llm_validation=use_llm_validation,
            llm_client=llm_client,
            llm_model=llm_model,
            client_type=client_type
        )
        
        if not paths_dict:
            return (
                f"No paths found from '{source_keyword}' to nodes with label '{target_label}'.\n\n"
                f"Total nodes found: {summary.get('total_found', 0)}\n"
                f"Suggestion: Try different entity names or check available node types."
            )
        
        # Prioritize validated valid paths
        if summary.get('validation_enabled'):
            validated_valid = [(node, info) for node, info in paths_dict.items()
                              if info.get('validated') and info.get('validation_result', {}).get('valid')]
            not_validated = [(node, info) for node, info in paths_dict.items()
                            if not info.get('validated')]
            
            validated_valid.sort(key=lambda x: x[1]['path_length'])
            not_validated.sort(key=lambda x: x[1]['path_length'])
            
            if validated_valid:
                sorted_paths = validated_valid[:max_results]
                result_source = "validated and confirmed valid"
            else:
                sorted_paths = not_validated[:max_results]
                result_source = "not yet validated (assumed valid)"
        else:
            sorted_paths = sorted(paths_dict.items(), key=lambda x: x[1]['path_length'])[:max_results]
            result_source = "no validation performed"
        
        # Format output
        result_lines = []
        
        # Header
        if summary.get('validation_enabled'):
            result_lines.append(
                f"✅ Found {summary['valid_paths']}/{summary['validated_paths']} valid paths "
                f"from '{source_keyword}' to '{target_label}' (validated by {llm_model})\n"
            )
        else:
            result_lines.append(
                f"✅ Found {len(sorted_paths)} paths from '{source_keyword}' to '{target_label}'\n"
            )
        
        # Preview top 3 paths
        display_count = min(3, len(sorted_paths))
        result_lines.append(f"Top {display_count} shortest paths:")
        
        for i, (target, info) in enumerate(sorted_paths[:display_count], 1):
            validation_mark = ""
            if info.get('validated'):
                validation_mark = " ✅" if info.get('validation_result', {}).get('valid') else " ❌"
            
            result_lines.append(
                f"\n{i}. {target}{validation_mark} (length: {info['path_length']})"
            )
            result_lines.append(f"   {info['path_string']}")
        
        # Selected path details
        if sorted_paths:
            selected_target, selected_info = sorted_paths[0]
            result_lines.append(f"\n{'='*60}")
            result_lines.append(f"📍 SELECTED PATH (Primary Focus):")
            result_lines.append(f"  • Target: '{selected_target}'")
            if selected_info.get('validated'):
                validation_result = selected_info.get('validation_result', {})
                if validation_result.get('valid'):
                    result_lines.append(f"  • Status: ✅ Validated by {llm_model}")
                else:
                    result_lines.append(f"  • Status: ❌ {validation_result.get('reason', 'Invalid')}")
            result_lines.append(f"  • Length: {selected_info['path_length']} nodes")
            result_lines.append(f"  • Path: {selected_info['path_string']}")
            result_lines.append(f"{'='*60}\n")
        
        # All validated paths
        result_lines.append(f"\n{'='*70}")
        result_lines.append(f"📋 ALL {len(sorted_paths)} VALIDATED PATHS:")
        result_lines.append(f"{'='*70}")
        
        for i, (target, info) in enumerate(sorted_paths, 1):
            validation_mark = ""
            if info.get('validated'):
                validation_mark = " ✅" if info.get('validation_result', {}).get('valid') else " ❌"
            
            result_lines.append(
                f"\n{i}. {target}{validation_mark} (length: {info['path_length']})"
            )
            result_lines.append(f"   {info['path_string']}")
        
        result_lines.append(f"\n{'='*70}")
        
        # Instructions for ontologist
        num_to_analyze = min(10, len(sorted_paths))
        
        result_lines.append(f"\n{'='*70}")
        result_lines.append(f"📋 INSTRUCTIONS FOR ONTOLOGIST:")
        result_lines.append(f"{'='*70}")
        result_lines.append(f"You have received {len(sorted_paths)} candidate paths above.\n")
        result_lines.append(f"Task: Select and analyze the TOP {num_to_analyze} most scientifically interesting paths.\n")
        result_lines.append(f"Required Format:")
        result_lines.append(f"1. PATH SELECTION: List all {num_to_analyze} selected paths with brief selection rationale")
        result_lines.append(f"2. DETAILED ANALYSIS: For each of the {num_to_analyze} paths, provide:")
        result_lines.append(f"   - Key Definitions (2-4 sentences per term)")
        result_lines.append(f"   - Relationship Analysis (2-3 sentences per relationship)")
        result_lines.append(f"   - Clinical/Research Significance (2-3 sentences)")
        result_lines.append(f"\nSelection Criteria (prioritize paths with):")
        result_lines.append(f"  • Novel biological connections")
        result_lines.append(f"  • Clear therapeutic or biomarker potential")
        result_lines.append(f"  • Strong clinical relevance")
        result_lines.append(f"  • Mechanistic clarity and testability")
        result_lines.append(f"  • Diversity across different mechanisms")
        result_lines.append(f"\nNote: Ensure all {num_to_analyze} paths are analyzed in detail.")
        result_lines.append(f"{'='*70}\n")
        
        return "\n".join(result_lines)
    
    except Exception as e:
        error_msg = (
            f"❌ Error finding paths from '{source_keyword}' to '{target_label}':\n"
            f"{str(e)}\n\n"
            f"Error type: {type(e).__name__}"
        )
        print(f"[ERROR] {error_msg}")
        return error_msg


def create_find_paths_to_label_tool(
    G,
    embedding_tokenizer,
    embedding_model,
    node_embeddings,
    llm_client=None,
    use_llm_validation=True,
    llm_model="gpt-4o-mini",
    client_type="openai"
):
    """
    Create a pre-configured find_shortest_paths_to_label_for_agent function.
    
    This factory function returns a tool function bound with graph and LLM client,
    ready to be passed to an agent.
    """
    def tool_function(
        source_keyword: Annotated[str, "A SPECIFIC entity name from the graph"],
        target_label: Annotated[str, "A node TYPE that exists in the graph"],
        max_results: Annotated[int, "Maximum number of shortest paths to return"] = 30,
        label_field: Annotated[str, "Field name for node labels in the graph"] = "type",
        validate_nodes: Annotated[bool, "Whether to filter out invalid node names"] = True,
    ) -> str:
        """Find shortest paths from a SPECIFIC entity to ALL entities of a certain TYPE."""
        return find_shortest_paths_to_label_for_agent(
            G=G,
            embedding_tokenizer=embedding_tokenizer,
            embedding_model=embedding_model,
            node_embeddings=node_embeddings,
            source_keyword=source_keyword,
            target_label=target_label,
            max_results=max_results,
            label_field=label_field,
            validate_nodes=validate_nodes,
            use_llm_validation=use_llm_validation,
            llm_client=llm_client,
            llm_model=llm_model,
            client_type=client_type,
        )
    
    return tool_function


# ==================== VISUALIZATION ====================
def visualize_paths_to_label(paths_dict, summary, top_n: int = 10):
    """Visualize path results (sorted by path length, display top N)."""
    if not paths_dict:
        print("No paths found!")
        return
    
    sorted_paths = sorted(paths_dict.items(), key=lambda x: x[1]['path_length'])
    
    print(f"\n{'='*60}")
    print(f"Found {summary['successful']}/{summary['total_targets']} paths")
    print(f"{'='*60}\n")
    
    for i, (target, info) in enumerate(sorted_paths[:top_n], 1):
        print(f"{i}. Path to '{target}' (length: {info['path_length']})")
        print(f"   {info['path_string']}")
        print()
    
    if summary['failed'] > 0:
        print(f"\n⚠️  {summary['failed']} target nodes could not find paths")


def export_paths_to_csv(paths_dict, output_file: str = "paths_to_label.csv"):
    """Export path results to CSV file."""
    import csv
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Target Node', 'Path Length', 'Path String'])
        
        for target, info in sorted(paths_dict.items(), key=lambda x: x[1]['path_length']):
            writer.writerow([
                target,
                info['path_length'],
                info['path_string']
            ])
    
    print(f"✅ Paths exported to {output_file}")


# ==================== LITERATURE SEARCH ====================
def semanticscholar_search(query: str, max_results: int = 10) -> str:
    """
    Search Semantic Scholar academic database (includes preprints).
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        Formatted search results
    """
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
            print(f"🔑 Using Semantic Scholar API key (ends with: ...{api_key[-4:]})")
        else:
            print("⚠️ No Semantic Scholar API key found, using rate-limited access")
        
        params = {
            "query": query,
            "limit": max_results,
            "fields": "title,abstract,year,authors,venue,externalIds,url,publicationTypes"
        }
        
        time.sleep(0.5 if api_key else 1.5)
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        papers = data.get("data", [])
        
        if not papers:
            return f"No results found in Semantic Scholar for query: {query}"
        
        results = []
        for paper in papers:
            title = paper.get("title", "No title")
            abstract = paper.get("abstract", "No abstract available")
            year = paper.get("year", "Unknown")
            venue = paper.get("venue", "Unknown venue")
            url_link = paper.get("url", "")
            
            pub_types = paper.get("publicationTypes", [])
            is_preprint = "Preprint" in pub_types if pub_types else False
            
            venue_lower = venue.lower() if venue else ""
            if "biorxiv" in venue_lower:
                source = "bioRxiv (preprint)"
            elif "medrxiv" in venue_lower:
                source = "medRxiv (preprint)"
            elif "arxiv" in venue_lower:
                source = "arXiv (preprint)"
            elif is_preprint:
                source = f"{venue} (preprint)"
            else:
                source = venue
            
            external_ids = paper.get("externalIds", {})
            doi = external_ids.get("DOI", "")
            
            authors = paper.get("authors", [])
            author_names = [a.get("name", "") for a in authors[:3]]
            author_str = ", ".join(author_names)
            if len(authors) > 3:
                author_str += " et al."
            
            results.append({
                "title": title,
                "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
                "year": str(year),
                "source": source,
                "authors": author_str,
                "url": url_link,
                "doi": doi,
                "is_preprint": is_preprint
            })
        
        output = f"Semantic Scholar Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Authors: {paper['authors']}\n"
            output += f"   Source: {paper['source']}\n"
            output += f"   Year: {paper['year']}\n"
            if paper['doi']:
                output += f"   DOI: {paper['doi']}\n"
            output += f"   URL: {paper['url']}\n"
            if paper['abstract'] != "No abstract available":
                output += f"   Abstract: {paper['abstract']}\n"
            output += "\n"
        
        return output
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            return "Rate limit exceeded for Semantic Scholar. Please try again later."
        return f"Error searching Semantic Scholar: {str(e)}"
    except Exception as e:
        return f"Error searching Semantic Scholar: {str(e)}"


def pubmed_search(query: str, max_results: int = 10) -> str:
    """
    Search PubMed biomedical literature.
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        Formatted search results
    """
    try:
        # Step 1: Search for PubMed IDs
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance"
        }
        
        response = requests.get(search_url, params=search_params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        id_list = data.get("esearchresult", {}).get("idlist", [])
        
        if not id_list:
            return f"No results found in PubMed for query: {query}"
        
        # Step 2: Fetch detailed information
        time.sleep(0.4)
        
        fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(id_list[:max_results]),
            "retmode": "xml"
        }
        
        fetch_response = requests.get(fetch_url, params=fetch_params, timeout=10)
        fetch_response.raise_for_status()
        
        root = ET.fromstring(fetch_response.content)
        
        results = []
        for article in root.findall(".//PubmedArticle"):
            try:
                title_elem = article.find(".//ArticleTitle")
                title = title_elem.text if title_elem is not None else "No title"
                
                abstract_elem = article.find(".//AbstractText")
                abstract = abstract_elem.text if abstract_elem is not None else "No abstract"
                
                pmid_elem = article.find(".//PMID")
                pmid = pmid_elem.text if pmid_elem is not None else "Unknown"
                
                year_elem = article.find(".//PubDate/Year")
                year = year_elem.text if year_elem is not None else "Unknown"
                
                results.append({
                    "title": title,
                    "abstract": abstract[:300] + "..." if len(abstract) > 300 else abstract,
                    "year": year,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "pmid": pmid,
                    "source": "PubMed"
                })
            except Exception:
                continue
        
        output = f"PubMed Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Year: {paper['year']}\n"
            output += f"   PMID: {paper['pmid']}\n"
            output += f"   URL: {paper['url']}\n"
            output += f"   Abstract: {paper['abstract']}\n\n"
        
        return output
        
    except Exception as e:
        return f"Error searching PubMed: {str(e)}"


def arxiv_search(query: str, max_results: int = 10) -> str:
    """
    Search arXiv preprints.
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        Formatted search results
    """
    try:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom'
        }
        
        entries = root.findall('atom:entry', ns)
        
        if not entries:
            return f"No results found in arXiv for query: {query}"
        
        results = []
        for entry in entries:
            try:
                title = entry.find('atom:title', ns).text.strip()
                summary = entry.find('atom:summary', ns).text.strip()
                published = entry.find('atom:published', ns).text[:10]
                arxiv_id = entry.find('atom:id', ns).text.split('/abs/')[-1]
                
                results.append({
                    "title": title,
                    "abstract": summary[:300] + "..." if len(summary) > 300 else summary,
                    "year": published[:4],
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "arxiv_id": arxiv_id,
                    "source": "arXiv"
                })
            except Exception:
                continue
        
        output = f"arXiv Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Year: {paper['year']}\n"
            output += f"   arXiv ID: {paper['arxiv_id']}\n"
            output += f"   URL: {paper['url']}\n"
            output += f"   Abstract: {paper['abstract']}\n\n"
        
        return output
        
    except Exception as e:
        return f"Error searching arXiv: {str(e)}"


def openalex_search(query: str, max_results: int = 10) -> str:
    """
    Search OpenAlex academic database.
    
    Args:
        query: Search query
        max_results: Maximum number of results
    
    Returns:
        Formatted search results
    """
    try:
        url = "https://api.openalex.org/works"
        params = {
            "search": query,
            "per_page": max_results,
            "mailto": "your-email@example.com"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results_list = data.get("results", [])
        
        if not results_list:
            return f"No results found in OpenAlex for query: {query}"
        
        results = []
        for work in results_list:
            try:
                title = work.get("title", "No title")
                abstract = work.get("abstract", "No abstract available")
                year = work.get("publication_year", "Unknown")
                doi = work.get("doi", "")
                url = work.get("doi", work.get("id", ""))
                
                results.append({
                    "title": title,
                    "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
                    "year": str(year),
                    "url": url,
                    "source": "OpenAlex"
                })
            except Exception:
                continue
        
        output = f"OpenAlex Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Year: {paper['year']}\n"
            output += f"   URL: {paper['url']}\n"
            if paper['abstract'] != "No abstract available":
                output += f"   Abstract: {paper['abstract']}\n"
            output += "\n"
        
        return output
        
    except Exception as e:
        return f"Error searching OpenAlex: {str(e)}"


def europepmc_search(query: str, max_results: int = 10) -> str:
    """
    Search Europe PMC (European PubMed Central).
    
    Coverage: Biomedical literature, including more European research
    Advantage: Broader than PubMed, includes more open access content
    """
    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query": query,
            "format": "json",
            "pageSize": max_results,
            "resultType": "core"
        }
        
        time.sleep(0.5)
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results_list = data.get("resultList", {}).get("result", [])
        
        if not results_list:
            return f"No results found in Europe PMC for query: {query}"
        
        results = []
        for article in results_list:
            try:
                title = article.get("title", "No title")
                abstract = article.get("abstractText", "No abstract available")
                year = article.get("pubYear", "Unknown")
                pmid = article.get("pmid", "")
                pmcid = article.get("pmcid", "")
                doi = article.get("doi", "")
                
                if pmcid:
                    url_link = f"https://europepmc.org/article/PMC/{pmcid.replace('PMC', '')}"
                elif pmid:
                    url_link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                elif doi:
                    url_link = f"https://doi.org/{doi}"
                else:
                    url_link = "No URL available"
                
                source = article.get("journalTitle", article.get("source", "Unknown"))
                
                results.append({
                    "title": title,
                    "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
                    "year": str(year),
                    "source": source,
                    "url": url_link,
                    "pmid": pmid,
                    "pmcid": pmcid,
                    "doi": doi
                })
            except Exception:
                continue
        
        output = f"Europe PMC Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Source: {paper['source']}\n"
            output += f"   Year: {paper['year']}\n"
            if paper['pmid']:
                output += f"   PMID: {paper['pmid']}\n"
            if paper['pmcid']:
                output += f"   PMCID: {paper['pmcid']}\n"
            if paper['doi']:
                output += f"   DOI: {paper['doi']}\n"
            output += f"   URL: {paper['url']}\n"
            if paper['abstract'] != "No abstract available":
                output += f"   Abstract: {paper['abstract']}\n"
            output += "\n"
        
        return output
        
    except Exception as e:
        return f"Error searching Europe PMC: {str(e)}"


def biorxiv_search(query: str, max_results: int = 10) -> str:
    """
    Search bioRxiv and medRxiv preprints.
    
    Coverage: Latest preprints in biology and medicine
    Advantage: Access to research not yet formally published
    """
    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        enhanced_query = f"{query} AND (SRC:PPR OR SRC:MED)"
        
        params = {
            "query": enhanced_query,
            "format": "json",
            "pageSize": max_results,
            "resultType": "core"
        }
        
        time.sleep(0.5)
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results_list = data.get("resultList", {}).get("result", [])
        
        if not results_list:
            return f"No results found in bioRxiv/medRxiv for query: {query}"
        
        results = []
        for article in results_list:
            try:
                title = article.get("title", "No title")
                abstract = article.get("abstractText", "No abstract available")
                year = article.get("pubYear", "Unknown")
                doi = article.get("doi", "")
                
                source = article.get("source", "")
                if "biorxiv" in source.lower():
                    preprint_source = "bioRxiv"
                elif "medrxiv" in source.lower():
                    preprint_source = "medRxiv"
                else:
                    preprint_source = "Preprint"
                
                if doi:
                    if "biorxiv" in doi.lower():
                        url_link = f"https://www.biorxiv.org/content/{doi}"
                    elif "medrxiv" in doi.lower():
                        url_link = f"https://www.medrxiv.org/content/{doi}"
                    else:
                        url_link = f"https://doi.org/{doi}"
                else:
                    url_link = "No URL available"
                
                results.append({
                    "title": title,
                    "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
                    "year": str(year),
                    "source": preprint_source,
                    "url": url_link,
                    "doi": doi
                })
            except Exception:
                continue
        
        output = f"bioRxiv/medRxiv Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            output += f"   Source: {paper['source']} (Preprint)\n"
            output += f"   Year: {paper['year']}\n"
            if paper['doi']:
                output += f"   DOI: {paper['doi']}\n"
            output += f"   URL: {paper['url']}\n"
            if paper['abstract'] != "No abstract available":
                output += f"   Abstract: {paper['abstract']}\n"
            output += "\n"
        
        return output
        
    except Exception as e:
        return f"Error searching bioRxiv/medRxiv: {str(e)}"


def crossref_search(query: str, max_results: int = 10) -> str:
    """
    Search CrossRef academic database.
    
    Coverage: Interdisciplinary academic literature, strong citation data
    Advantage: Most comprehensive DOI database, excellent metadata quality
    """
    try:
        url = "https://api.crossref.org/works"
        email = os.environ.get("CROSSREF_EMAIL", "your-email@example.com")
        
        params = {
            "query": query,
            "rows": max_results,
            "mailto": email,
            "select": "title,author,published-print,DOI,abstract,container-title,type"
        }
        
        time.sleep(0.5)
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        items = data.get("message", {}).get("items", [])
        
        if not items:
            return f"No results found in CrossRef for query: {query}"
        
        results = []
        for item in items:
            try:
                title_list = item.get("title", [])
                title = title_list[0] if title_list else "No title"
                
                abstract = item.get("abstract", "No abstract available")
                
                published = item.get("published-print", item.get("published-online", {}))
                date_parts = published.get("date-parts", [[]])
                year = str(date_parts[0][0]) if date_parts and date_parts[0] else "Unknown"
                
                doi = item.get("DOI", "")
                url_link = f"https://doi.org/{doi}" if doi else "No URL available"
                
                container_title = item.get("container-title", [])
                source = container_title[0] if container_title else "Unknown"
                
                authors = item.get("author", [])
                author_names = []
                for author in authors[:3]:
                    given = author.get("given", "")
                    family = author.get("family", "")
                    if given and family:
                        author_names.append(f"{given} {family}")
                    elif family:
                        author_names.append(family)
                
                author_str = ", ".join(author_names)
                if len(authors) > 3:
                    author_str += " et al."
                
                pub_type = item.get("type", "unknown")
                
                results.append({
                    "title": title,
                    "abstract": abstract[:300] + "..." if abstract and len(abstract) > 300 else abstract,
                    "year": year,
                    "source": source,
                    "authors": author_str,
                    "url": url_link,
                    "doi": doi,
                    "type": pub_type
                })
            except Exception:
                continue
        
        output = f"CrossRef Search Results for: {query}\n"
        output += "=" * 70 + "\n\n"
        
        for i, paper in enumerate(results, 1):
            output += f"{i}. {paper['title']}\n"
            if paper['authors']:
                output += f"   Authors: {paper['authors']}\n"
            output += f"   Source: {paper['source']}\n"
            output += f"   Year: {paper['year']}\n"
            output += f"   Type: {paper['type']}\n"
            if paper['doi']:
                output += f"   DOI: {paper['doi']}\n"
            output += f"   URL: {paper['url']}\n"
            if paper['abstract'] != "No abstract available":
                output += f"   Abstract: {paper['abstract']}\n"
            output += "\n"
        
        return output
        
    except Exception as e:
        return f"Error searching CrossRef: {str(e)}"


def _filter_results_by_date(result_text: str, date_end: str, source_name: str) -> str:
    """
    Post-process search results to filter out papers published after date_end.

    This function parses the formatted search result text and removes entries
    where the publication date exceeds date_end. Used for "Time Travel" backtesting.

    Args:
        result_text: Formatted search results string
        date_end: Maximum allowed publication date (inclusive), format "YYYY-MM" (e.g., "2019-06")
        source_name: Name of the source for logging

    Returns:
        Filtered search results string
    """
    import re

    if not result_text or "No results found" in result_text or "Error" in result_text:
        return result_text

    # Parse the date_end
    try:
        year_end, month_end = date_end.split('-')
        year_end = int(year_end)
        month_end = int(month_end)
    except (ValueError, AttributeError):
        # Fallback: treat as year only
        year_end = int(date_end) if str(date_end).isdigit() else 2099
        month_end = 12

    # Month name to number mapping
    month_names = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
        'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
        'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'september': 9,
        'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12
    }

    lines = result_text.split('\n')
    filtered_lines = []
    current_entry = []
    current_entry_year = None
    current_entry_month = None
    skip_entry = False
    entries_removed = 0
    entries_kept = 0

    # Patterns to match dates in various formats
    # Pattern 1: "Year: 2020" (year only)
    year_only_pattern = re.compile(r'Year:\s*(\d{4})\s*$', re.IGNORECASE)
    # Pattern 2: "Year: 2020-06" or "Date: 2020-06"
    year_month_pattern = re.compile(r'(?:Year|Date|Published):\s*(\d{4})-(\d{1,2})', re.IGNORECASE)
    # Pattern 3: "Published: June 2020" or "Date: Jun 2020"
    month_year_pattern = re.compile(r'(?:Year|Date|Published):\s*(\w+)\s+(\d{4})', re.IGNORECASE)
    # Pattern 4: "2020-06-15" format in any context
    iso_date_pattern = re.compile(r'(\d{4})-(\d{1,2})-\d{1,2}')
    # Pattern to detect start of a new entry (numbered like "1.", "2.", etc.)
    entry_start_pattern = re.compile(r'^\d+\.\s+')

    def should_skip(paper_year, paper_month):
        """Check if paper should be skipped based on date."""
        if paper_year is None:
            return False  # Keep if we can't determine year
        if paper_year > year_end:
            return True
        if paper_year == year_end and paper_month is not None and paper_month > month_end:
            return True
        return False

    for line in lines:
        # Check if this is the start of a new entry
        if entry_start_pattern.match(line.strip()):
            # Process previous entry if exists
            if current_entry:
                if not skip_entry and not should_skip(current_entry_year, current_entry_month):
                    filtered_lines.extend(current_entry)
                    entries_kept += 1
                else:
                    entries_removed += 1
            current_entry = [line]
            current_entry_year = None
            current_entry_month = None
            skip_entry = False
        else:
            current_entry.append(line)

        # Try to extract date from current line
        # Priority: year-month > month year > year only

        # Try year-month pattern (2020-06)
        ym_match = year_month_pattern.search(line)
        if ym_match:
            try:
                current_entry_year = int(ym_match.group(1))
                current_entry_month = int(ym_match.group(2))
                continue
            except ValueError:
                pass

        # Try ISO date pattern (2020-06-15)
        iso_match = iso_date_pattern.search(line)
        if iso_match:
            try:
                current_entry_year = int(iso_match.group(1))
                current_entry_month = int(iso_match.group(2))
                continue
            except ValueError:
                pass

        # Try month year pattern (June 2020)
        my_match = month_year_pattern.search(line)
        if my_match:
            try:
                month_str = my_match.group(1).lower()
                if month_str in month_names:
                    current_entry_month = month_names[month_str]
                    current_entry_year = int(my_match.group(2))
                    continue
            except ValueError:
                pass

        # Try year only pattern
        y_match = year_only_pattern.search(line)
        if y_match and current_entry_year is None:
            try:
                current_entry_year = int(y_match.group(1))
                # If only year, assume December (conservative: include full year)
                current_entry_month = 12
            except ValueError:
                pass

    # Process last entry
    if current_entry:
        if not skip_entry and not should_skip(current_entry_year, current_entry_month):
            filtered_lines.extend(current_entry)
            entries_kept += 1
        else:
            entries_removed += 1

    if entries_removed > 0:
        print(f"   🕒 Time Travel Filter ({source_name}): Removed {entries_removed} papers after {date_end}, kept {entries_kept}")

    # Reconstruct the result
    filtered_text = '\n'.join(filtered_lines)

    # If all entries were filtered out, return a message
    if entries_kept == 0 and entries_removed > 0:
        header_lines = []
        for line in lines:
            if entry_start_pattern.match(line.strip()):
                break
            header_lines.append(line)
        header = '\n'.join(header_lines)
        return f"{header}\n[No papers found published before {date_end}]"

    return filtered_text


# Keep backward compatibility alias
def _filter_results_by_year(result_text: str, year_end: int, source_name: str) -> str:
    """Backward compatibility wrapper for _filter_results_by_date."""
    return _filter_results_by_date(result_text, f"{year_end}-12", source_name)


def multi_source_literature_search(
    query: str,
    sources: Optional[List[str]] = None,
    max_results_per_source: int = 5,
    date_end: Optional[str] = None,
    year_end: Optional[int] = None  # Deprecated, use date_end
) -> str:
    """
    Search literature from multiple sources and merge results.

    Args:
        query: Search query
        sources: List of data sources, options:
                ["semantic_scholar", "pubmed", "arxiv", "openalex",
                 "europepmc", "biorxiv", "crossref"]
        max_results_per_source: Maximum results per source
        date_end: Optional maximum publication date (inclusive), format "YYYY-MM".
                  If set, papers published after this date are filtered out.
                  Used for "Time Travel" backtesting mode.
                  Example: "2019-06" filters out papers after June 2019.
        year_end: DEPRECATED - use date_end instead. If provided, converts to "YYYY-12".

    Returns:
        Merged search results (filtered by date if date_end is specified)
    """
    if sources is None:
        sources = ["semantic_scholar", "pubmed"]

    # Handle backward compatibility: convert year_end to date_end
    if year_end and not date_end:
        date_end = f"{year_end}-12"

    if date_end:
        print(f"🕒 TIME TRAVEL MODE ACTIVE: Filtering papers to ≤ {date_end}")

    all_results = []

    search_functions = {
        "semantic_scholar": semanticscholar_search,
        "semanticscholar": semanticscholar_search,
        "pubmed": pubmed_search,
        "arxiv": arxiv_search,
        "openalex": openalex_search,
        "europepmc": europepmc_search,
        "europe_pmc": europepmc_search,
        "biorxiv": biorxiv_search,
        "medrxiv": biorxiv_search,
        "crossref": crossref_search,
    }

    for source in sources:
        source_normalized = source.lower().replace("_", "").replace("-", "")

        search_func = None
        for key, func in search_functions.items():
            key_normalized = key.lower().replace("_", "").replace("-", "")
            if key_normalized == source_normalized:
                search_func = func
                break

        if search_func:
            try:
                print(f"🔍 Searching {source}...")
                result = search_func(query, max_results=max_results_per_source)

                # Apply date filter if date_end is specified
                if date_end and result:
                    result = _filter_results_by_date(result, date_end, source)

                all_results.append(result)
                time.sleep(1.5)
            except Exception as e:
                print(f"⚠️ Error searching {source}: {e}")
                all_results.append(f"Error searching {source}: {str(e)}")
        else:
            print(f"⚠️ Unknown source: {source}")
            print(f"   Available sources: {list(search_functions.keys())}")

    if not all_results:
        return f"No results found across any sources for query: {query}"

    combined = "\n\n" + "="*80 + "\n\n".join(all_results)
    return combined

# ==================== TESTING UTILITIES ====================
def test_fallback(G):
    """Test fallback mechanism for node type expansion."""
    print("🧪 Testing Type Fallback Mechanism")
    print("=" * 70)
    
    # Test 1: Cancer (should include Disease)
    print("\nTest 1: Finding 'Cancer' type")
    cancer_nodes = get_nodes_by_label(G, "Cancer")
    cancer_fallback_nodes = get_nodes_by_label_with_fallback(G, "Cancer")
    
    print(f"  Original function found: {len(cancer_nodes)} nodes")
    print(f"  New function found: {len(cancer_fallback_nodes)} nodes")
    print(f"  Increase: {len(cancer_fallback_nodes) - len(cancer_nodes)} nodes")
    
    if len(cancer_fallback_nodes) > len(cancer_nodes):
        print(f"\n  Sample of new nodes (first 10):")
        new_nodes = [n for n in cancer_fallback_nodes if n not in cancer_nodes]
        for i, node in enumerate(new_nodes[:10], 1):
            node_type = G.nodes[node].get('type', 'NO_TYPE')
            print(f"    {i}. {node} [type={node_type}]")
    
    # Test 2: Gene (should include Genetic)
    print("\nTest 2: Finding 'Gene' type")
    gene_nodes = get_nodes_by_label(G, "Gene")
    gene_fallback_nodes = get_nodes_by_label_with_fallback(G, "Gene")
    
    print(f"  Original function found: {len(gene_nodes)} nodes")
    print(f"  New function found: {len(gene_fallback_nodes)} nodes")
    print(f"  Increase: {len(gene_fallback_nodes) - len(gene_nodes)} nodes")
    
    # Test 3: Disease (no fallback, should be same)
    print("\nTest 3: Finding 'Disease' type (no fallback)")
    disease_nodes = get_nodes_by_label(G, "Disease")
    disease_fallback_nodes = get_nodes_by_label_with_fallback(G, "Disease")
    
    print(f"  Original function found: {len(disease_nodes)} nodes")
    print(f"  New function found: {len(disease_fallback_nodes)} nodes")
    print(f"  Increase: {len(disease_fallback_nodes) - len(disease_nodes)} nodes")
    
    print("\n" + "=" * 70)
    print("✅ Testing Complete")

# ==================== END OF FILE ====================
if __name__ == "__main__":
    print("Knowledge Graph Reasoning and Literature Search Utilities loaded successfully.")
    print("Available functions:")
    print("  - Path generation: generate_path, create_path, create_multi_keyword_path")
    print("  - Node operations: get_nodes_by_label, get_nodes_by_label_with_fallback")
    print("  - Path finding: find_paths_to_label, find_shortest_paths_to_label_for_agent")
    print("  - Literature search: semanticscholar_search, pubmed_search, arxiv_search, etc.")
    print("  - Multi-source search: multi_source_literature_search")