# SAGE — Third-Party Notices and Provenance

This file identifies third-party projects relevant to SAGE and records where their code is actually reused, modified, or redistributed. **Complete the inventory before publishing.** Naming a project below does not itself mean that its code has been incorporated.

## 1. SciAgents — Apache License 2.0

- **Project:** SciAgents — *Automating Scientific Discovery Through Bioinspired Multi-Agent Intelligent Graph Reasoning*
- **Authors:** A. Ghafarollahi and M. J. Buehler
- **Research paper:** https://arxiv.org/abs/2409.05556
- **Upstream repository:** https://github.com/lamm-mit/SciAgentsDiscovery
- **License:** Apache License, Version 2.0
- **Upstream revision or release used:** `[ADD COMMIT HASH OR TAG]`
- **Local full license text:** `[ADD PATH, e.g. licenses/Apache-2.0.txt]`
- **Upstream copyright and NOTICE files:** `[RECORD AND INCLUDE AS REQUIRED]`

SAGE reuses or adapts selected SciAgents functions. The original Apache 2.0 permissions on those portions remain intact. Preserve applicable copyright and attribution notices and mark files with modifications prominently.

### SciAgents file inventory

| File in SAGE | Original SciAgents file or function | Copied / modified / adapted | Summary of changes |
| --- | --- | --- | --- |
| `[sage/path/to/file.py]` | `[upstream/path/to/file.py::function]` | `[type]` | `[changes]` |

For files adapted from SciAgents, add a prominent source-file header such as:

```python
# Portions derived from SciAgents:
# https://github.com/lamm-mit/SciAgentsDiscovery
# Original portions licensed under Apache License 2.0.
# Modified by the SAGE development team, [YEAR].
# Changes: [BRIEFLY DESCRIBE CHANGES].
# Retain all applicable original copyright and license notices.
# See THIRD_PARTY_NOTICES.md.
```

## 2. Paperclip — MIT License

- **Project:** Paperclip
- **Upstream repository:** https://github.com/paperclipai/paperclip
- **License:** MIT License
- **Relationship to SAGE:** `[SELECT: source incorporated / modified source / redistributed dependency / external integration only / not used]`
- **Upstream revision or release used:** `[ADD COMMIT HASH OR TAG, IF APPLICABLE]`
- **Local full license text and original copyright notice:** `[ADD PATH IF REDISTRIBUTED, e.g. licenses/MIT-Paperclip.txt]`

**If Paperclip code is redistributed:** include the exact MIT copyright and permission notice supplied with the upstream version. Identify any copied or modified source files below. The MIT terms remain applicable to Paperclip code regardless of the license chosen for original SAGE components.

**If Paperclip is used only as a separately installed external tool or service:** record that fact here; this file should not misleadingly claim Paperclip code is bundled.

### Paperclip file inventory (if applicable)

| File in SAGE | Original Paperclip file or package | Copied / modified / redistributed | Summary of changes |
| --- | --- | --- | --- |
| `[sage/path/to/file]` | `[upstream/path/to/file]` | `[type]` | `[changes]` |

## 3. Other third-party materials

Add an entry for every further reused or redistributed component, including any code from `https://github.com/lamm-mit/GraphReasoning` if used. Record the original source, exact revision, license, copyright notices, modification status, and corresponding SAGE files.

| Component | Upstream source and revision | License | Local file(s) | Required notices included? |
| --- | --- | --- | --- | --- |
| `[name]` | `[URL + revision]` | `[license]` | `[paths]` | `[yes/no]` |

## 4. Important distinction

The presence of Apache- or MIT-licensed components does **not** require SAGE's separately original components to use those same licenses. Conversely, a restrictive SAGE license cannot eliminate the rights that the original upstream licenses granted for their covered code.

See `LICENSING.md` for the overall licensing structure. This notice file does not replace the upstream license texts.
