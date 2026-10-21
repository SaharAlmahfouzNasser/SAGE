## Phase 1: Mathematical Metrics (Fast Filtering)

Rapidly evaluate multiple generated paths using computationally efficient mathematical metrics to perform initial filtering and ranking. This stage enables fast screening of large path sets (e.g., Top-100 paths) without requiring expensive LLM API calls. The total score contains 4 parts of evaluations: Logic, Relevance, Novelty and Surprise.

---

### A. Logic

**Goal:** Remove "fake paths" that contain semantic breaks.

**Method:** Compute the semantic similarity between the query and the generated path.

**Formula:**
$$
\text{Score}_{\text{logic}} = \text{CosSim}(\mathbf{v}_{query}, \mathbf{v}_{path})
$$

where:
- $\mathbf{v}_{query}$ is the embedding vector of the user query $Q$
- $\mathbf{v}_{path}$ is the embedding vector of the path $P$ (converted to natural language)
- $\text{CosSim}$ is the cosine similarity function

**Implementation Steps:**

1. **Vectorization:**  
   Encode the user query $Q$ using a Sentence-BERT model (default: `all-MiniLM-L6-v2`) to obtain the vector $\mathbf{v}_{query}$.

2. **Path Embedding:**  
   Convert the generated path $P$ into a natural-language sentence by:

   - Removing entity type markers (e.g., `||Phenotype`, `||Gene`)
   - Concatenating entities and relations in natural order
   - Example: `"tertiary lymphoid structures||Phenotype -- promote -- immunotherapy||Process"` 
     → `"tertiary lymphoid structures promotes immunotherapy"`
   - Encode the sentence using the same Sentence-BERT model to obtain $\mathbf{v}_{path}$

3. **Similarity Computation:**  
   Compute the cosine similarity between the two vectors.

   
   $$
   \text{CosSim}(\mathbf{v}_1, \mathbf{v}_2) = \frac{\mathbf{v}_1 \cdot \mathbf{v}_2}{||\mathbf{v}_1|| \cdot ||\mathbf{v}_2||} = \cos(\theta)
   $$


**Output:**

- `logic_score`: Cosine similarity value (0.0 to 1.0)

---

### B. Relevance

**Goal:** Measure how relevant the path is to the target research domain.

**Formula:**
$$
\text{Score}_{\text{rel}} = 1 - \frac{1}{N} \sum_{e \in P} || \mathbf{e} - \mathbf{c}_{target} ||_2
$$

where:
- $P$ is the set of entities in the path
- $N = |P|$ is the number of entities in the path
- $\mathbf{e}$ is the L2-normalized embedding vector of entity $e$
- $\mathbf{c}_{target}$ is the L2-normalized embedding vector of the target domain (e.g., "bladder cancer")
- $|| \cdot ||_2$ is the Euclidean distance (L2 norm)

**Implementation Details:**

1. **Target Domain Embedding:**
   - Get the embedding vector for the target domain (e.g., "bladder cancer") from node embeddings file
   
   - Apply L2 normalization: 
     $$
     \mathbf{c}_{target} = \frac{\mathbf{c}_{target}}{||\mathbf{c}_{target}||_2}
     $$
     
   
2. **Entity Embedding Extraction:**
   
   - For each entity $e$ in the path:
     - Extract entity name (remove type markers like `||Gene`)
     
     - Get embedding vector from node embeddings
     
     - Apply L2 normalization: 
       $$
       \mathbf{e} = \frac{\mathbf{e}}{||\mathbf{e}||_2}
       $$
       
   
3. **Distance Calculation:**
   
   - Compute Euclidean distance: 
     $$
     d_e = ||\mathbf{e} - \mathbf{c}_{target}||_2
     $$
     
   - Calculate average distance: 
     $$
     \bar{d} = \frac{1}{N} \sum_{e \in P} d_e
     $$
     
   
4. **Score Mapping:**
   
   - Map the normalized distance to [0, 1] range:
     $$
     \text{Score}_{\text{rel}} = 1 - \frac{\bar{d}}{2}
     $$
   - Clamp the score to [0, 1] to ensure valid range

**Intuition:**

- Smaller distance → Higher relevance (entities are closer to the target domain in embedding space)
- The score measures how "centered" the path is around the target domain

**Output:**

- `relevance_score`: Relevance score (0.0 to 1.0)

---

### C. Novelty

**Goal:** Measure how novel or rare the entities in the path are.

**Formula:**

$$
\text{Score}_{\text{nov}} = \frac{1}{|P|} \sum_{e \in P} -\log(P(e))
$$

where:
- $P$ is the set of entities in the path
- $|P|$ is the number of entities
- $P(e)$ is the occurrence probability of entity $e$ in the knowledge graph
- $-\log(P(e))$ is the negative log-probability (information content)

**Probability Calculation:**

The occurrence probability $P(e)$ is computed as:

$$
P(e) = \frac{\text{degree}(e) + \epsilon}{\sum_{v \in V} \text{degree}(v) + \epsilon}
$$

where:
- $\text{degree}(e)$ is the node degree of entity $e$ in the knowledge graph
- $V$ is the set of all nodes in the graph
- $\epsilon$ is a small constant (1e-8) to avoid division by zero

**Implementation Steps:**

1. **Entity Degree Extraction:**
   
   - For each entity $e$ in the path, get the node degree from the knowledge graph $G$
   
2. **Probability Computation:**
   
   - Calculate total degree sum: 
     $$
     D_{total} = \sum_{v \in V} \text{degree}(v)
     $$
     
   - For each entity: 
     $$
     P(e) = \frac{\text{degree}(e) + \epsilon}{D_{total} + \epsilon}
     $$
     
   
3. **Information Content:**
   
   - Compute $-\log(P(e))$ for each entity
   - Rare entities (low degree) → high $-\log(P(e))$ → high novelty
   - Common entities (high degree) → low $-\log(P(e))$ → low novelty
   
4. **Average and Normalization:**
   
   - Calculate average: 
     $$
     \bar{H} = \frac{1}{|P|} \sum_{e \in P} -\log(P(e))
     $$
     
   - Normalize using min-max scaling across all nodes in the graph:
     $$
     \text{Score}_{\text{nov}} = \frac{\bar{H} - H_{min}}{H_{max} - H_{min}}
     $$
     
   - where $H_{min}$ and $H_{max}$ are the minimum and maximum $-\log(P(e))$ values across all nodes in $G$

**Intuition:**

- Rare entities (low degree) contribute more to novelty
- The score rewards paths containing understudied or less-connected entities
- Normalization ensures scores are comparable across different graphs

**Output:**
- `novelty_score`: Normalized novelty score (0.0 to 1.0)
- `novelty_raw_score`: Raw average $-\log(P(e))$ value (before normalization)

---

### D. Surprise

**Goal:** Measure the "cross-domain" extent of the path, i.e., how much the path deviates from conventional domain expectations.

**Method:** Compute the KL divergence between the path's actual category distribution and the expected category distribution from the global knowledge graph.

**Formula:**

$$
\text{Score}_{\text{surprise}} = \text{KL}(P_{actual} || P_{expected})
$$

where:
- $P_{actual}$ is the category distribution of entities in the path
- $P_{expected}$ is the expected category distribution from the global knowledge graph (conventional domain)
- $\text{KL}(P || Q)$ is the Kullback-Leibler divergence

**KL Divergence Definition:**

$$
\text{KL}(P || Q) = \sum_{c \in \mathcal{C}} P(c) \log\left(\frac{P(c)}{Q(c)}\right)
$$

where:
- $\mathcal{C}$ is the set of all entity categories (e.g., Gene, Protein, Disease, Phenotype)
- $P(c)$ is the probability of category $c$ in the path
- $Q(c)$ is the expected probability of category $c$ in the global graph

**Implementation Steps:**

1. **Extract Entity Categories:**
   - Parse the path string to extract entity types (e.g., `||Gene`, `||Protein`, `||Phenotype`)
   - Build category list: $[c_1, c_2, \ldots, c_N]$ where $N$ is the number of entities

2. **Compute Actual Distribution:**
   
   - Count occurrences of each category in the path
   - Normalize to get probability distribution:
     $$
     P_{actual}(c) = \frac{\text{count}(c)}{N}
     $$
   
3. **Compute Expected Distribution:**
   - Count occurrences of each category across all nodes in the global graph $G$
   - Normalize to get expected probability distribution:
     $$
     P_{expected}(c) = \frac{\text{count}_{global}(c)}{\sum_{c' \in \mathcal{C}} \text{count}_{global}(c')}
     $$

4. **Calculate KL Divergence:**
   
   - For each category $c$:
     $$
     \text{KL} += P_{actual}(c) \log\left(\frac{P_{actual}(c)}{P_{expected}(c)}\right)
     $$
   
5. **Normalization:**
   
   - Compute KL divergence for all paths
   - Apply min-max normalization:
     $$
     \text{Score}_{\text{surprise}} = \frac{\text{KL} - \text{KL}_{min}}{\text{KL}_{max} - \text{KL}_{min}}
     $$
   - Clamp to [0, 1] range

**Intuition:**

- High surprise: Path contains categories that are unexpected in the conventional domain
- Example: A path connecting "drug" → "microbiome" → "disease" has high surprise if the global graph shows drugs typically connect to proteins, not microbiome entities
- The KL divergence measures how "surprising" the category distribution is compared to the baseline

**Output:**
- `surprise_score`: Normalized surprise score (0.0 to 1.0)
- `surprise_raw_score`: Raw KL divergence value (before normalization)

---

### E. Weighted Total Score

**Goal:** Combine all four metrics into a single weighted score for ranking.

**Formula:**
$$
\text{Score}_{\text{total}} = w_{logic} \cdot S_{logic} + w_{relevance} \cdot S_{relevance} + w_{novelty} \cdot S_{novelty} + w_{surprise} \cdot S_{surprise}
$$

where:
- $S_{logic}$, $S_{relevance}$, $S_{novelty}$, $S_{surprise}$ are the normalized scores (0.0 to 1.0)
- $w_{logic}$, $w_{relevance}$, $w_{novelty}$, $w_{surprise}$ are the weights (sum to 1.0)

**Default Weights (Encouraging Innovation):**

- $w_{novelty} = 0.3$
- $w_{surprise} = 0.3$
- $w_{logic} = 0.2$ (20%)
- $w_{relevance} = 0.2$ (20%)

**Output:**

- `weighted_total_score`: Combined score (0.0 to 1.0)
- `score_components`: Dictionary with individual weighted contributions
- `score_weights`: Dictionary with the weights used

---

### F. Path Sorting and Renumbering

**Goal:** Sort paths by their weighted total score and renumber them accordingly.

**Method:**

- Sort paths in descending order by `weighted_total_score`
- Renumber paths starting from 1 (highest score = Path 1, next highest = Path 2, etc.)

**Output:**

- Sorted path list with updated `number` field

---

## Phase 2: LLM-as-a-Judge (Refined Ranking)

For the Top-5 or Top-10 paths that pass Phase 1,  an LLM is used as an "expert reviewer" to perform deep evaluation and scoring.

**Model:** `gpt-4o`

### Evaluation Process

1. **Input Formatting:**
   - Format top-N paths with their Phase 1 scores
   - Include user query and target domain context
   - Structure: Path number, path string, target entity, length, and all four metric scores

2. **LLM Evaluation:**
   - Agent receives formatted input
   - Evaluates each path across four dimensions
   - Provides:
     - **Per-path scores** (0.0-1.0) for Logic, Relevance, Novelty, Surprise
     - **Justifications** (2-3 sentences per dimension)
     - **Overall assessment** (strengths, weaknesses, research potential)
   
3. **Comparative Analysis:**
   - Agent compares all evaluated paths
   - Identifies:
     - Best overall path
     - Most novel path
     - Most surprising path
     - Most relevant path
   - Provides final recommendation

### System Prompt

```
You are an expert biomedical path evaluator. Evaluate knowledge graph paths across four dimensions to identify promising research connections.

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
```



### Output Format

```
### PATH 1: DDIT4

**Path:** tertiary lymphoid structures||Phenotype -- are associated with -- TILs||CellType -- is expressed on -- PD-L1||Protein -- up-regulates -- BCG||Vaccine -- was remethylated after -- DDIT4||Gene  
**Path Length:** 9 entities

#### Evaluation Scores:

**Logic Score: 0.6**
- **Justification:** The path draws logical connections between tertiary lymphoid structures, TILs, and DDIT4, but introducing BCG, a vaccine, and remethylation weakens the coherence in directly addressing gene interaction in cancer. 

**Relevance Score: 0.5**
- **Justification:** While the path includes relevant entities like PD-L1 and TILs, which are significant in bladder cancer immunotherapy, the focus on BCG remethylation diverts from gene-specific pathways integral to bladder cancer.

**Novelty Score: 0.8**
- **Justification:** DDIT4 and its association with remethylation is understudied, hinting at novel explorations, though PD-L1 and TILs are well-known entities in this context.

**Surprise Score: 0.9**
- **Justification:** The inclusion of a vaccine and a remethylation event introduces an unexpected cross-domain element, connecting immunological processes with gene regulation.

**Overall Assessment:**
- **Strengths:** Introduces novel cross-domain interactions with potential exploration in gene regulation mechanisms.
- **Weaknesses:** Incoherent elements such as BCG weaken it when focusing specifically on bladder cancer.
- **Research Potential:** Promising for investigating novel gene regulatory pathways and therapeutic strategies involving DDIT4.

// ... other paths

### COMPARATIVE ANALYSIS:

**Best Overall Path:** Path 1 - Offers strong novelty and surprise, with potential new lines of inquiry in gene regulation.

**Most Novel Path:** Path 1 - Involves rare gene interactions and unexplored paths.

**Most Surprising Path:** Path 1 - Strong unexpected cross-domain elements with vaccine and remethylation.

**Most Relevant Path:** Path 5 - Immunotherapy and mutation links offer relevant therapeutic insights.

**Recommendation:** Path 1 is recommended for its novelty and surprising potential, particularly in exploring new gene regulatory mechanisms. Complement this with Path 5's relevance in therapeutics to enhance research breadth in bladder cancer domain-specific strategies.
```
