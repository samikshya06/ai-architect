<!-- source: https://www.uber.com/in/en/blog/uber-cart-assistant/ | provider: jina -->

Title: Cart Assistant: Agentic Grocery Shopping on Uber Eats

URL Source:

Markdown Content:

*   Explore Image 1 Products Advertising Learn more about advertising on Uber. Reach consumers as they go anywhere and get anything.Earn Resources for driving and delivering with UberRide Experiences and information for people on the moveEat Ordering meals for delivery is just the beginning with Uber EatsMerchants Putting stores within reach of a world of customersBusiness Transforming the way companies move and feed their peopleHealth Moving care forward together with medical providersHigher Education Enhancing campus transportationTransit Expanding the reach of public transportation  Image 2 Company Engineering The technology behind Uber EngineeringCommunity support Doing the right thing for cities and communities globallyNewsroom Uber news and updates in your countryUber.com Product, how-to, and policy content—and more
*   More

    *   No results

*    EN

*   Sign up

*   Sign up

June 16, 2026

# Cart Assistant: Agentic Grocery Shopping on Uber Eats

AB

Anurag Biyani

Principal Engineer

DK

Deepak Kumar Sahoo

Staff Software Engineer

RB

Renato Beserra Sousa

Staff Software Engineer

Share this article

# Introduction

Grocery shopping often begins outside a commerce app: a handwritten list on the fridge, a screenshot of a recipe, or a vague plan like “healthy breakfasts for the week.” Translating that raw intent into a useful grocery cart is more complex than a keyword search. The system has to understand the goal, map it to store inventory, choose relevant items and quantities, and respect constraints, all while leaving the shopper in control.

Earlier this year, we launched Cart Assistant in beta on Uber Eats to make the shopping process easier. The product was also featured at GO-GET, Uber’s annual product showcase. With Cart Assistant, shoppers can enter a prompt or upload an image, and the system creates a draft grocery cart they can review and edit before placing an order.

This blog describes the system behind it: a multi-prompt state graph for planning, relevance judging, quantity selection, constraint enforcement, guardrails, and evaluation.

Figure 1: Example Cart Assistant flow

### **From Search-First to Cart-First Shopping**

The traditional flow in grocery apps is search-first. Shoppers break their intent into individual queries, compare products, decide quantities, and build the cart item by item:

_intent -> ([manual search -> manual product selection] x N) -> cart -> checkout._

Cart Assistant shifts the flow to:

_intent -> draft cart -> user review -> checkout._

The important change is that shoppers no longer have to manually translate every intent into individual search queries and product choices.

### **Architecture: A Multi-Prompt State Graph**

Cart Assistant uses a multi-prompt state graph: a workflow in which each step owns a focused piece of reasoning, data fetching, or validation. Each LLM call has a narrow responsibility and structured output, with deterministic APIs, validation layers, and state transitions connecting the calls. LLMs handle ambiguity; deterministic systems handle retrieval, pricing, eligibility, schema validation, aggregation, and cart construction.

Figure 2: Simplified architecture

**Stage****Responsibility****Mechanism**
Cart Plan Generation Interpret text or image input, classify intent, and produce planned items with search terms, item context, quantity context, and constraints LLM structured output
Candidate Retrieval and Enrichment Query store inventory and enrich candidates with catalog metadata, availability, price, deals, promotions, and sellable-unit information Backend search and catalog APIs
Semantic Relevance Judging Determine which candidates best satisfy each planned item in context LLM rubric with structured output
Price and Deal Constraint Enforcement Apply item-level price limits, sale or deal preferences, eligibility filters, and cart-level price limits Deterministic constraint optimization
Quantity Selection Convert human quantities into purchasable units or pick a reasonable default LLM reasoning and deterministic arithmetic/validation
Guardrails Validate schemas, supported intents, prompt-safety boundaries, product constraints, and generated content Deterministic and LLM-based checks
Cart Assembly Aggregate per-item results and construct the cart to be returned Deterministic aggregator
Content Refinement Generate concise shopper-facing text, recipe instructions, or meal-plan guidance when needed LLM structured output

#### Cart Plan Generation

Figure 3: A simplified pseudo-schema of the Cart Plan output

Each planned item becomes an independent unit of work that can be searched, re-ranked, filtered, and quantity-resolved in parallel.

Shopper requests can be complex. A single prompt may contain a recipe and a shopping list with additional constraints. For example: _“I want to cook pasta for two. Also add paper towels and vegan protein powder; keep the protein powder under $20.”_ The planner needs to capture and preserve all the information in structured fields for subsequent steps.

The planner also separates retrieval language from reasoning context. For example, the query: _“I want to make gluten-free lasagna”_ leads to one of the planned items being: _{..., “search\_terms”: ["gluten-free pasta"], “item\_context”: “for lasagna; gluten-free is a strict user constraint”, …}_. This separation lets the retrieval system receive a concise query, while later LLM steps receive the reason the item is needed.

Price constraints are captured at the right level of the plan. If the shopper says “milk under $5,” the planner attaches that limit to the milk item. If the shopper says “eggs, bread, and milk under $30 total,” the planner records a cart-level limit that later stages enforce using actual store prices, availability, deals, and promotions. Similarly, requests like “snacks on sale,” “deals on cereal,” or “cheapest bacon” become structured signals that backend ranking and filtering can apply.

#### Semantic Relevance and Constraints

After the planner creates items, Cart Assistant retrieves candidate products from store inventory. The next question is: which product best satisfies this planned item in context?

We use a dedicated semantic relevance step with a constrained rubric. The rubric helps the model distinguish direct matches, reasonable variants, acceptable substitutes, adjacent products, and poor matches, without turning the decision into free-form text.

Context matters. If the shopper asks for “tomatoes for stew,” canned diced tomatoes may be a strong candidate. If they ask for “tomatoes for salad,” fresh tomatoes are more likely to be appropriate. If they say “no dairy,” the system should pick vegan cheese over conventional cheese.

We also separate semantic relevance from price and quantity. A product can be relevant but too expensive, not aligned with a sale or deal request, or available only in an impractical package size. Splitting these concerns makes the system easier to debug and evaluate.

#### Quantity Selection

Automatically selecting the right number of units for each item is one of the most deceptively complex parts of the system. Shoppers frequently express quantities in colloquial terms: “12 eggs,” “3 tomatoes,” “2 cups of flour,” “snacks for a party,” or “a weekly breakfast plan for two kids.” Stores sell products in catalog terms: cartons, bunches, pounds, multipacks, fluid ounces, and so on.

Cart Assistant combines deterministic calculations with a dedicated LLM reasoning step. The model reasons over quantity context and packaging details; deterministic arithmetic and validation then ensure the answer maps to purchasable units.

Several examples illustrate why this deserves its own stage:

*   “12 eggs” should resolve to one 12-count carton, not 12 cartons.
*   “8 water bottles” may resolve to an 8-pack, a 12-pack, two 6-packs, or individual bottles, depending on available products and package-size tolerance.
*   “2 cups of flour” or “1 cup of basil” require different volume-to-weight assumptions because product form and density matter.

The quantity step uses a data trust hierarchy: Product titles and descriptions often contain human-readable packaging clues, while structured fields provide confirmation or fallback. Common grocery knowledge helps resolve remaining ambiguity, and deterministic checks ensure the result is sellable.

The planner also preserves quantity provenance. There’s a meaningful difference between explicit quantities and system-estimated quantities. “12 eggs” should be respected closely; eggs inferred for a pancake recipe can be rounded to practical package sizes.

#### Guardrails and Safe Execution

Cart Assistant accepts open-ended natural language and image inputs, but the system itself operates within a bounded shopping workflow. Guardrails enforce that boundary across shopper input, intermediate cart state, and shopper-facing generated content. They help the assistant distinguish grocery-shopping requests from unsupported intents, resist prompt-injection and instruction-hijacking attempts, and keep both cart decisions and generated text grounded in catalog data, cart state, and supported product capabilities.

We split guardrails into deterministic and LLM-based checks. Deterministic checks validate structured outputs, required fields, enum values, price constraints, quantity calculations, product eligibility, availability, and whether selected items can actually be assembled into a valid cart. LLM-based checks handle fuzzier decisions, such as whether a request is in-domain, whether an instruction is attempting to override system behavior, or whether recipe, meal-plan, substitution, or cart-summary text is appropriate and faithful to the selected products.

This gives the system multiple opportunities to fail safely. A malformed plan can be rejected before retrieval. An unsupported request can be routed to a safe response instead of being forced into a cart. A relevant product can still be filtered if it violates price, deal, preference, or eligibility constraints. Generated content can also be revised or suppressed if it isn’t grounded in the cart or the shopper’s request. By treating guardrails as part of the state graph, rather than a final moderation pass, we make each stage easier to inspect, debug, and evaluate.

#### Parallelism and Latency

A grocery request can contain many planned items. Processing them sequentially would make latency scale directly with the number of items.

By processing items concurrently via dynamic asynchronous execution paths, we shifted the latency bottleneck from sequential per-item latency to bounded parallel batches. Performance is now primarily dominated by the slowest item path and our concurrency limits, rather than the raw number of items. When dependencies allow, guardrails and content refinement run asynchronously from the main per-item loop rather than blocking every item-resolution path.

We also use a mix of model sizes, reasoning settings, and prompt tuning to keep LLM latency under control.

### Evaluation-Driven Development

LLMs are powerful, but they’re also highly sensitive stochastic systems. Small changes can produce surprisingly large shifts in behavior. In traditional systems, a small code change usually has a relatively predictable blast radius that can be reasoned about from first principles. With LLMs, that isn’t always the case. A prompt update that improves recipe requests may hurt simple shopping lists. A tiny change in phrasing, punctuation, or formatting can sometimes produce results that feel disproportionate to the change and are difficult to explain from first principles.

In systems like these, you can’t confidently improve what you can’t reliably measure. This makes evaluations a core part of the development loop, not an afterthought.

For Uber’s Cart Assistant, we built an eval-driven development framework that evaluates changes against a broad corpus of curated synthetic requests and anonymized production-derived edge cases. The framework uses two complementary layers:

*   **Strict deterministic verification:** Rule-based checks for objective correctness, such as item identification, expected item inclusion, schema validity, quantity handling, constraint propagation, and expected failure behavior.
*   **LLM-as-a-judge**: A multimodal LLM judge calibrated with human input assesses semantic and experiential quality across dimensions including overall experience, search-term quality, constraint adherence, and text response quality.

This creates a practical workflow for both engineers and AI agents working on the system: change the system, run evals against the baseline and candidate versions, inspect regressions with step-level traces, and decide whether to fix the behavior or explicitly accept the trade-off. The resolution may involve changing the product logic, or in some cases, refining the judge rubric when traces show that the evaluator, rather than the product behavior, is incorrectly scoring a case.

We also apply the same evaluation system to production-query analysis, helping us monitor how effectively the assistant handles real user needs.

# Conclusion

Building Cart Assistant challenged us to rethink how people interact with digital storefronts. Grocery shopping is inherently personal, messy, and bound by real-world constraints.

By decomposing the problem into a state-graph architecture, we replaced the unpredictability of a monolithic LLM prompt with a more controllable and inspectable pipeline: focused model calls surrounded by deterministic retrieval, validation, and constraint enforcement. The result is a system that can turn messy grocery intent into a useful draft cart while preserving the shopper’s ability to review, edit, and decide.
