# SAGE — Licensing and Third-Party Software

**SAGE** (Structured Agentic Framework for Generating and Evaluating Scientific Hypotheses in Computational Pathology) includes original software and third-party components. **Different portions of this repository are subject to different licenses.** The license for a particular third-party file or component takes precedence over this overview.

## 1. Original SAGE contributions — CC BY-NC-ND 4.0

**Copyright © 2026 Sahar Almahfouz Nasser, Anant Madabhushi, and Emory University, to the extent of their respective ownership rights.**

Subject to confirmation of all relevant copyright holders' authorization, the original SAGE materials designated as such in this repository are offered under the **Creative Commons Attribution–NonCommercial–NoDerivatives 4.0 International License (CC BY-NC-ND 4.0)**:

https://creativecommons.org/licenses/by-nc-nd/4.0/legalcode

Under this license, recipients may copy and redistribute the licensed original material for **noncommercial purposes**, with appropriate attribution and the required license notice. Recipients may make adaptations privately, but **may not share adapted material** without separate authorization. Commercial uses require separate authorization from the relevant rights holders. These provisions do not prevent rights holders from granting separate permissions or commercial licenses.

**Important:** CC BY-NC-ND 4.0 is *not* an open-source software license, and Creative Commons recommends software-specific licenses for code. It does not automatically authorize sharing modified versions or imply permission to use copyrighted third-party material on the same terms. If a user needs permission to distribute modifications, they should request it from the SAGE rights holders.

The rights holders' names above are supplied by the project maintainers, **not an independently verified determination of legal title or institutional authorization**. Before releasing under this grant, the maintainers must confirm that Sahar Almahfouz Nasser, Anant Madabhushi, and Emory University (where it holds rights) have authorized the release. The existence of this draft does not substitute for that authorization.

## 2. SciAgents / GraphReasoning — Apache License 2.0

SAGE reuses or adapts functions from **SciAgents: Automating Scientific Discovery Through Bioinspired Multi-Agent Intelligent Graph Reasoning**, by Alireza Ghafarollahi and Markus J. Buehler.

- Repository: https://github.com/lamm-mit/SciAgentsDiscovery
- Publication: https://arxiv.org/abs/2409.05556
- Upstream license: Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0

SciAgents-derived material retains the rights granted under its applicable Apache 2.0 license, including commercial permissions. Preserve its license, relevant copyright notices, applicable upstream NOTICE material, and prominent notices identifying modified files when distributing covered material. Do not represent upstream Apache-licensed code as exclusively licensed under CC BY-NC-ND 4.0.

## 3. Microsoft AutoGen-derived code — MIT License

The `modifiedAutogen/` directory is described in the project README as containing adaptations of AutoGen console and agent tooling. Where its files incorporate Microsoft AutoGen code, preserve the MIT license and its copyright/permission notice from the particular upstream revision used.

- Repository: https://github.com/microsoft/autogen
- Source-code license: https://github.com/microsoft/autogen/blob/main/LICENSE-CODE

AutoGen documentation and other non-code assets can carry different licenses. The MIT license's permissions in original AutoGen material remain unaffected by SAGE-specific restrictions.

## 4. Paperclip — MIT License (only if used)

- Repository: https://github.com/paperclipai/paperclip
- Upstream software license: MIT

Paperclip's inclusion in this document **does not assert that it is used in or distributed with SAGE**. If any Paperclip code is incorporated, retain the license and copyright notices for the exact revision. Merely interacting with a separately installed Paperclip service does not constitute redistribution of its source.

## 5. Documentation, figures, datasets, and dependencies

Original SAGE documentation and original figures may also be released under CC BY-NC-ND 4.0 where the rights holders authorize their inclusion in that license. Third-party images, other datasets, pretrained weights, services, and software packages remain subject to their separate licenses, agreements, and privacy obligations. The SAGE license does not override any of those terms.

## 6. Academic attribution

Users are requested to cite the SAGE publication when reporting results obtained using SAGE and, where relevant, to cite SciAgents and GraphReasoning. Scholarly citation requests are separate from legal attribution and notice requirements.

- **SAGE:** *SAGE: Evidence-First Biomarker Discovery through Multi-Agent Reasoning.* Advances in Neural Information Processing Systems (NeurIPS 2026), as currently listed in the repository README; verify against the final proceedings record before publishing citation metadata.
- **SciAgents:** Ghafarollahi, A. and Buehler, M. J. (2024). *SciAgents: Automating Scientific Discovery Through Bioinspired Multi-Agent Intelligent Graph Reasoning.* Advanced Materials, 2413523.
- **GraphReasoning:** Buehler, M. J. (2024). *Accelerating Scientific Discovery with Generative Knowledge Extraction, Graph-Based Representation, and Multimodal Intelligent Graph Reasoning.* Machine Learning: Science and Technology. https://doi.org/10.1088/2632-2153/ad7228

## 7. Commercial licensing and additional permissions

For permission beyond CC BY-NC-ND 4.0, including commercial deployment or distribution of modified original SAGE components, contact the SAGE maintainers through the repository's GitHub Issues or its listed maintainer contact. Permissions must be granted by parties with the necessary authority; use of the project name does not itself confer permission for clinical deployment or regulated medical use.

## 8. Third-party precedence and warranty

Nothing here limits rights that third-party authors granted under Apache 2.0, MIT, or another applicable license. In particular, upstream permissive-license materials remain usable under their original terms even when distributed alongside restricted SAGE contributions. Each file's provenance and accompanying notices should be checked before redistribution.

The software is provided **AS IS**, without warranties to the extent permitted by law. Where a third-party license contains a separate disclaimer, that disclaimer continues to apply.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for an attribution inventory. The repository must also include any required verbatim upstream license and NOTICE files before redistribution.
