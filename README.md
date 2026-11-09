# AI Architect

AI Architect turns a system-design request into an AWS component selection, a
partial cost estimate, and a topology diagram. The workflow runs in this order:

1. Requirements agent
2. Capacity calculator
3. Component selection agent
4. Cost estimation
5. Design agent

## 1. Requirements agent

The requirements agent uses the configured `llm` model to understand the request
and collect information that affects the architecture. It asks for daily active
users if missing, always asks about a monthly budget ceiling, and may ask a few
additional questions about topics such as regions, latency, or compliance. Questions
are presented interactively through the `ask_user` tool.

Its validated `RequirementSpec` output separates the system type, explicit
requirements, clarifications, and sizing assumptions. This distinction helps
downstream stages distinguish stated requirements from assumed workload values.
Conversation state is checkpointed per thread in PostgreSQL, with summarization for
long conversations; the separately stored requirement-spec database write is
currently disabled.

## 2. Capacity calculator

The capacity calculator uses the requirements and formulas for the selected system
type to estimate workload and sizing metrics. Depending on the system, these can
include peak read/write QPS, concurrent connections, storage growth, and network
throughput. Each output row includes a numeric value, unit, formula, and the
downstream capacity it drives.

Sizing assumptions come from the supplied specification, system-specific defaults,
or general defaults. Calculations themselves are deterministic; a small LLM is used
only when a free-form system type needs to be mapped to a formula-library category.
The results are estimates based on those inputs, not measured production traffic.

## 3. Component selection agent

The component selection agent uses the configured `llm` model to choose AWS
components based on the requirement specification, capacity results, architecture
knowledge, and AWS documentation. Its structured `ComponentChoices` output
describes **what to select**: service, region, instance type, and other relevant
service options. Each component has a unique ID, rationale, and `based_on`
references; usage and pricing are handled downstream.

### RAPTOR architecture knowledge

RAPTOR provides architecture patterns and trade-offs relevant to the requested
system. Its indexing pipeline chunks the reference documents in
`architectural-ref-patterns/arch-ref-docs`, embeds them, clusters related chunks,
and creates summaries recursively. The resulting hierarchy is stored in Qdrant.

At selection time, the requirement and capacity results are used to retrieve
relevant summary nodes, explore their children by vector similarity, and supplement
the results with a direct search of leaf chunks. Results are deduplicated by node
ID. Retrieval does not rebuild or modify the index; an empty collection or search
failure is reported as an error.

The `raptor` settings in `config.yaml` bound retrieval: `retrieval_summary_top_k`
selects starting summaries, `retrieval_max_depth` limits child hops,
`retrieval_branch_width` limits children per summary, `retrieval_max_branch_nodes`
caps branch nodes, and `retrieval_leaf_top_k` limits direct leaf results. At most
the branch-node cap plus the leaf limit are returned. When
`retrieval_max_child_candidates` is exceeded, the first child IDs in stored order
are considered.

Retrieval logs counts and selected node IDs, levels, similarity scores, and source
metadata; debug logs also show parent-child links. Document text and requirement
queries are not logged by default. `retrieval_log_text_previews: true` enables
up to 160 characters of each retrieved node at DEBUG level. Previews and source
metadata may contain sensitive information, so enable them with care.

### AWS documentation through MCP

The agent also receives tools from the AWS documentation MCP server, started with
`uvx` for the duration of component selection. MCP (Model Context Protocol) exposes
documentation tools to the model so it can check AWS-specific service capabilities
and identifiers. RAPTOR supplies architecture context; AWS documentation is used
to verify concrete AWS product details. The server subprocess closes after the
selection run.

### Traceability and validation

Components cite requirement fields, clarification keys, assumptions, sizing metrics,
or knowledge-base evidence. RAPTOR citations use `kb:<source filename>`; AWS
documentation citations use an exact `kb:<HTTPS URL>` returned by a successful tool
call. Source filenames come from Qdrant metadata and may not be original web URLs.
Multi-source summaries list contributing documents but do not prove every source
supports every statement.

Validation checks requirement, clarification, and assumption references against the
specification and checks KB filenames or URLs against retrieved evidence. It does
not validate sizing references or prove that cited text supports the choice. Unknown
references are sent back for correction; repeated failures are reported explicitly.

## 4. Cost estimation

**Status: partially implemented; further service and cost coverage is needed.**
This deterministic stage uses the selected components and capacity results, not an
LLM. It fetches current public AWS Price List files without AWS credentials and
matches region, instance type, and service-specific options. Downloads are not
cached; regional EC2 files can be several hundred MB and require network access,
temporary disk space, and time.

Currently it estimates instance hours for EC2, RDS, and ElastiCache, plus
on-demand DynamoDB read/write request units. Other selected services are marked
unestimated. Costs such as storage, backups, data transfer, and extended support
are excluded. Each scenario reports a `known_monthly_usd` partial amount and a
`complete` flag; missing prices, ambiguous SKUs, and download failures are reported
on affected components rather than treated as free.

The output compares on-demand with standard one- and three-year reservations using
Partial Upfront and All Upfront options. It assumes 730 hours per month and
steady-state instance capacity; upfront fees are amortized in monthly totals and
shown separately. Public list prices exclude discounts, taxes, and free-tier
allowances. Missing reservation prices are not replaced with on-demand rates.

DynamoDB estimates assume average read/write rates of 10% of peak QPS, one
non-transactional operation per application operation, a 50/50 mix of strong and
eventual reads, and the calculator's average item size per operation. The estimator
does not assign this workload to multiple DynamoDB components or regions, or when
required metrics are missing. Provisioned capacity, storage, indexes, backups,
replication, transactions, and other charges remain unestimated. These assumptions
are approximations, not measured usage; review the reported issues and assumptions
before relying on the totals or purchasing reservations.

## 5. Design agent

The design composer uses the configured `llm` model (not `small_llm`) and the
requirements, capacity results, component selections, and cost estimate to arrange
the selected parts into a coherent system. It records read and write paths, cache
and queue choices, availability zones, datastore failover, assumptions, and risks.
Gaps or changes that require additional components should be called out rather
than treated as already provisioned or priced.

Its output is a typed `TopologyGraph`: nodes represent selected components, external
actors, or accounting entries, while directed edges describe data flows, protocols,
and estimated volumes or explicit unknowns. Validation checks IDs, edge endpoints,
selected component coverage, service and region, and referenced capacity metrics.
These checks do not prove architectural correctness or complete cost coverage.

Each successful run prints the outputs, returns the topology JSON from
`handle_query`, and writes a uniquely named Markdown document under `designs/`.
The document contains an editable Mermaid flowchart and component, flow, decision,
risk, and assumption details. Open the file in VS Code and use **Ctrl+Shift+V** to
preview it in a Mermaid-compatible Markdown renderer. Editing the Mermaid source
does not update the original topology JSON. Generated documents may contain
sensitive information and are excluded from Git by default; write failures are
reported. A design critic and graphical diagram editor are not yet implemented.