# RAPTOR retrieval

Component selection searches the Qdrant RAPTOR collection with the requirement and
capacity results. It finds relevant summary nodes, explores their children by vector
similarity, and supplements the branch results with a direct search of leaf chunks.
Results are deduplicated by node ID. This is query-time retrieval; it does not rebuild
or modify the collection.

The `raptor` settings in `config.yaml` bound the work: `retrieval_summary_top_k`
selects starting summaries, `retrieval_max_depth` limits child hops,
`retrieval_branch_width` limits selected children per summary,
`retrieval_max_branch_nodes` caps total branch nodes, and `retrieval_leaf_top_k`
limits the separate leaf search. At most the branch-node cap plus the leaf limit
are returned. `retrieval_max_child_candidates` caps the number of child IDs
considered per summary; when exceeded, the first IDs in stored order are considered.
Search errors are propagated, and an empty collection produces an explicit error.

Retrieval logs counts and each selected node's ID, level, similarity score, and
source metadata; debug logs also show selected parent-child links. Document text and
requirement queries are not logged by default. Set `retrieval_log_text_previews: true`
to log up to 160 characters of each retrieved node at DEBUG level. Previews and
source metadata may contain sensitive information, so enable them with care.

Component `based_on` citations use `kb:<source filename>` for retrieved RAPTOR
documents and `kb:<HTTPS URL>` for AWS documentation links returned by MCP tools.
Source filenames come from Qdrant payload metadata and are not necessarily original
web URLs. Multi-source summaries list contributing documents; they do not prove
that each document supports every statement. The component agent must choose an
appropriate source rather than citing an ambiguous multi-source summary ID.
Citation validation checks that filenames were retrieved or URLs appeared in tool
responses, not that the cited text actually supports the component choice.