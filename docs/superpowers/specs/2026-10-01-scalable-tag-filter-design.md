# Scalable Tag Filter Design

## Goal

Keep question retrieval efficient when the personal math question bank contains many tags. The tag control must support direct access to frequent tags, discovery of infrequent tags, and a stable view of the active filter state.

## Scope

This change applies to the tag area of the question retrieval page. It preserves the existing subject, section, mastery, knowledge-card, due-date, free-text, saved-filter, hierarchy, redirect, and merge behavior. It deliberately changes legacy archived-tag query handling as defined below.

## Interaction Model

### Active Tags

The top of the tag area renders every selected tag as a removable chip. A selected tag stays visible even when it does not appear in the current common-tag list, search result, or expanded category.

### Tag Search

The tag area provides a server-backed prefix search field. JavaScript debounces calls to a same-origin tag-suggestion endpoint and renders at most 20 matching active canonical tags with their category and count. Matching uses a trimmed, case-insensitive tag-name prefix. A search result checkbox uses the existing GET `tag` parameter and form submission flow.

Without JavaScript, the picker query is submitted to the existing list route as `tag_picker_q`. The response renders at most 20 matching active canonical tags as native checkboxes. The picker query changes only the picker presentation and never changes the question-result query.

### Common Tags

The page computes common tags from the number of non-deleted, non-archived questions associated with each active canonical tag.

* No selected subject: aggregate across the entire question bank.
* One or more selected subjects: count only questions from those subjects.
* Rank by count descending, then tag name and ID for deterministic output.
* Render the top eight tags with a nonzero count.
* A selected tag outside the top eight remains available in the active-tag row.

Question counts shown in this picker use the subject scope only. Other active filters do not change the count, so a user can distinguish tag popularity from a temporarily empty result set.

### Category Browsing

An active root tag with at least one active descendant represents a category. It remains selectable through a native `All in <category>` checkbox. Its count is the number of distinct searchable questions carrying the root tag or any active descendant, matching the existing recursive parent-tag search semantics.

Every active descendant is assigned to its highest active root ancestor and remains selectable. A descendant count similarly includes its active descendants. Nested descendants are flattened beneath the root category with a visible hierarchy depth marker. A tag whose direct parent or any ancestor is archived starts a new active root chain.

An active root tag with no active descendants is an independent tag. Independent tags render in an `Other` category and remain selectable. This definition uses the existing single `parent` relation and adds no new data field.

The initial view shows up to six selectable tags per category, ordered with the same count rule. It renders at most 12 categories, ordered by their total distinct-question count. The page exposes additional category members through the picker search instead of adding an unbounded DOM list. JavaScript can request additional members for one chosen category in pages of 20. Without JavaScript, `tag_picker_group` and `tag_picker_page` render that same bounded page through the list route.

The initial categories are configured through the existing tag-management hierarchy. Recommended root categories are knowledge module, problem type, method, and error pattern. The feature does not add a new tag-type model field.

### Counts and Empty States

Every selectable tag displays an associated-question count. Counts include only non-deleted, non-archived questions, matching the default question retrieval view. In this design, an active canonical tag has `archived=False` and `redirect_to IS NULL`. Archived tags and redirect sources stay excluded from normal picker lists. Categories and the common area hide zero-count tags unless that canonical tag is selected. Empty search results provide a link to tag management.

For a legacy URL containing an archived tag ID or a non-archived redirect source, the server follows the redirect chain to an active canonical target when one exists. A source without an active canonical target is ignored for query filtering and omitted from the active-filter row.

### Scale Boundaries

The target is up to 5,000 tags. A normal retrieval response renders at most eight common tags, 12 category headings, six members per category, 20 picker-search matches, and the user's selected controls. It never renders every tag solely for picker discovery.

The tag-suggestion endpoint performs a bounded prefix query against active canonical tags and returns at most 20 matches. It does not return all tags to the client. Add a migration for a composite `archived`, `redirect_to`, `name` index that supports this query. The 5,000-tag regression suite verifies the index exists and the suggestion response remains bounded.

The tag hierarchy used by category browsing is cached for five minutes and invalidated after tag create, rename, reparent, merge, redirect, archive, or restore operations. Category membership, subtree identifiers, and canonical relation IDs come from that cached hierarchy. Count aggregation uses bounded database queries over the requested category members and selected-subject scope.

## Server Contract

The question-list view canonicalizes requested tag IDs before filtering. An active canonical requested ID remains itself. A redirected source or archived requested ID becomes its active canonical redirect target when available. A source without an active canonical redirect target and an invalid ID are ignored. When every requested ID is ignored, the tag dimension applies no filter.

For each canonical tag, the hierarchy cache also stores its relation IDs: the canonical ID plus every historical source ID whose redirect chain ends at that canonical ID. A tag filter uses the union of relation IDs for the selected canonical tag and all canonical descendants. This retains questions whose historical many-to-many relation still points at a merged source tag. Every picker count uses this same relation-ID union and `Count(DISTINCT question_id)`, so the displayed count matches the default retrieval result.

The active-filter row displays canonical names and values. On a normal form submission it emits only canonical `tag` inputs, so an old redirected parameter is naturally replaced. A selected tag always has one native checkbox control in a dedicated `Selected tags` fieldset. The common, category, and search lists omit selected tags to avoid duplicate checkbox values. This lets a non-JavaScript user uncheck a selected zero-count or long-tail tag and submit the existing Apply button.

The question-list view adds a tag-picker context structure containing:

* selected tags
* the common-tag list
* bounded root categories, their visible descendants, and selected category page
* per-tag counts in the selected-subject scope

Counts must use database aggregation and avoid an N+1 query for tags, parents, or question relations. The implementation uses the cached active hierarchy, then aggregates distinct question IDs in bounded queries. Picker counts aggregate canonical active tags; redirected historical tags are not displayed as separate choices.

The tag model and management form must reject direct and indirect parent cycles. The hierarchy builder still detects cycles in legacy database rows using a visited-ID traversal. A detected cycle is excluded from category ancestry and its active canonical members render as independent `Other` tags for that request. The server logs the cycle IDs for maintenance and does not enter an unbounded traversal.

The existing `tag` query parameters remain the only server filter input. Multiple selected tags retain the existing OR behavior within the tag filter dimension and AND behavior with other filter dimensions.

## Accessibility and Responsive Behavior

The picker uses native checkbox controls in labels. Category expansion uses real buttons with `aria-expanded` and `aria-controls`. Search results are announced through a polite live region. Keyboard users can tab through every visible control and remove active tags without relying on pointer input.

At desktop width, common tags and category groups follow the existing top filter-panel layout. At narrow widths, groups use one column and controls retain a 40px minimum touch height. Long tag names wrap without changing the control width unexpectedly.

## Failure Handling

When JavaScript is unavailable, the server renders selected tags, common tags, the bounded category entries, and a bounded picker-search result as standard checkboxes. Category pages use ordinary links that preserve the active question filters and picker query. Search remains optional enhancement and does not block submission.

Malformed and archived-without-target tag query values are ignored by the updated search service. A merged or archived tag with an active redirect target resolves to that canonical target.

## Tests

Add tests covering:

* subject-scoped and all-subject common-tag ordering, default-retrieval question counts, subtree counts, and deterministic ties
* exclusion of archived and zero-count tags from common and category views
* active canonical tag visibility outside the common list and its native no-JavaScript removal control
* root-category selection, independent-tag `Other`, nested descendants, archived-parent chains, and category truncation
* active redirect resolution, non-archived redirect-source handling, historic source-to-canonical selected mapping, all-invalid requested IDs, and exclusion of sources without an active redirect target
* historical source-tag question relations retained in canonical filtering and distinct picker counts
* direct and indirect parent-cycle rejection plus safe legacy-cycle rendering
* bounded server picker search and category paging while preserving question-result filters
* response bounds, tag-suggestion query limit, hierarchy-cache invalidation, composite search-index migration, and query-count regression limits for a fixture with 5,000 tags
* template contracts for counts, search hooks, active tags, and native fallback controls
* browser behavior for asynchronous search, category page loading, keyboard state, active-tag removal, and server fallback submission when Playwright is available

## Out of Scope

No synonym model, tag aliases, fuzzy search, tag recommendation model, saved tag presets, or automatic tag classification is included in this iteration.
