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

# AWS cost estimation

After component selection, the workflow runs a deterministic cost estimator; it does
not use an LLM. It fetches current public AWS Price List files without AWS credentials,
and matches the selected region, instance type, and service-specific options. Each
priced component reports its source URL, publication date, SKU, term effective dates,
and a monthly amount. The files are downloaded for each estimate, not cached between
runs. Regional EC2 files can be several hundred MB, and the download and streaming
scan require network access, temporary disk space, and time.

The estimator prices instance hours for EC2, RDS, and ElastiCache, and on-demand
DynamoDB read and write request units. Other selected services are explicitly marked
unestimated; no cost is invented for them. These estimates omit charges such as storage, backups,
data transfer, and extended support. Every scenario therefore reports a
`known_monthly_usd` partial amount and a `complete` flag. Missing or ambiguous SKUs,
missing reservation terms, and failed downloads are reported on the affected
components rather than substituted with a remembered rate or a zero-cost estimate.

The output compares on-demand with standard 1-year and 3-year reserved instances
using both Partial Upfront and All Upfront payment options. It assumes every selected
EC2, RDS, and ElastiCache instance runs continuously and is steady-state, with 730
hours per month. Upfront fees are shown separately and amortized over 12 or 36 months
in the monthly figures; these are not the actual monthly cash payments. Public list
prices exclude taxes, credits, free-tier allowances, discounts, and negotiated rates.
Reservation prices that are unavailable are not replaced with on-demand prices.
Evaluate commitments and steady-state capacity before buying reservations.

Usage-driven components, including data transfer, are flagged as variable; capacity
components are marked predictable, subject to the excluded charges noted above.
For DynamoDB, the estimator approximates average read and write rates as 10% of the
capacity calculator's respective peak QPS, assumes one non-transactional DynamoDB
operation per application operation, and assumes a 50/50 mix of strongly and
eventually consistent reads. It treats the calculator's average item size as the size
of each operation, which can under- or overestimate request-unit rounding. The resulting
on-demand request costs are included in each reservation scenario; the DynamoDB requests
themselves are not reserved. The estimator reports these assumptions and the usage.
It refuses to assign the workload when there are multiple DynamoDB components or
multiple regions, or when the required peak metrics or item sizes are unavailable.
Provisioned DynamoDB capacity, storage, indexes, backups, replication, transactions,
and other charges remain unestimated. Peak-based approximations are not measurements
of actual average usage. The current component schema has no LLM inference service.

# Design composer

After cost estimation, the composer uses the configured `llm` model (not
`small_llm`) to turn the requirements, capacity metrics, selected components, and
cost estimate into a structured topology. `handle_query` returns the topology JSON;
the requirement specification, selections, and estimate are still printed. Each
successful run also writes a new Markdown document under `designs/` and prints its
path. Documents may contain sensitive requirements and architecture details; they
are excluded from Git by default. Write errors fail the run rather than being hidden.

The graph has uniquely identified nodes and directed edges suitable for an editable
diagram. Component nodes refer to the exact selected component IDs; external nodes
represent actors or systems outside the selection. Data-transfer cost entries are
accounting nodes, not infrastructure or edge endpoints. Edges include a protocol,
purpose, and either a volume estimate with its basis and capacity metric names or
an explicit unknown. The graph also records read and write paths, availability
zones, datastore failover, assumptions, and risks. A selected component is represented
once, including resources with multiple instances; its description explains its role.

Structural validation checks IDs, edge endpoints, selected component coverage,
service and region, and capacity metric references. The composer retries input-reference errors, then fails
explicitly. These checks do not prove the design, traffic estimates, availability,
protocol choices, or cost implications are correct. In particular, a partial cost
estimate is not a complete budget, and proposed additions must be selected and priced
before they can be treated as provisioned. No diagram editor or design critic is
implemented yet.

The document contains an editable Mermaid flowchart generated deterministically from
the validated graph, followed by component, data-flow, decision, risk, and assumption
details. Each arrow shows its protocol and expected volume (or `unknown`); accounting
nodes are labeled and disconnected. View it in a Markdown renderer that supports
Mermaid, such as a suitably configured VS Code Markdown preview, or edit the Mermaid
source directly. Edits to the document do not update the original topology JSON.