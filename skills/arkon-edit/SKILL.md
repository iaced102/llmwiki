---
name: arkon-edit
description: "Propose or directly apply edits to Arkon wiki pages. Contributors create drafts for review; editors/admins can edit directly. Triggers on: update wiki, fix this page, propose edit, edit wiki page, correct the KB, improve wiki."
allowed-tools: mcp__arkon__search_wiki mcp__arkon__read_wiki_index mcp__arkon__read_wiki_page mcp__arkon__propose_wiki_edit mcp__arkon__edit_wiki_page
---

# arkon-edit: Edit the Knowledge Base

Always read the current page before proposing changes. Always confirm with the user before submitting.

---

## Permission Tiers

| Role | Tool to use | Review required |
|------|------------|----------------|
| Contributor | `propose_wiki_edit` | Yes — goes to editor queue |
| Editor | `edit_wiki_page` | No — writes directly |
| Admin | `edit_wiki_page` | No — writes directly |

If you try `edit_wiki_page` and get a permission error, fall back to `propose_wiki_edit`.

---

## Workflow: Propose an Edit (Contributor)

1. **Find the page** — `search_wiki(query)` or `read_wiki_index()` to locate the slug.
2. **Read current content** — `read_wiki_page(slug)`. Never propose without reading first.
3. **Draft the edit** — produce the full updated Markdown (not a diff — the tool takes full content).
4. **Confirm with user** — show the diff or summary of changes. Get explicit approval.
5. **Submit** — `propose_wiki_edit(slug, content_md, note="one-line explanation")`.
6. Report the draft ID to the user so they can track it.

Do not submit a draft without user confirmation. The note field is important — editors need context.

---

## Workflow: Direct Edit (Editor/Admin)

Same steps 1-4 above, then:

5. **Submit** — `edit_wiki_page(slug, content_md, change_note="one-line explanation")`.
6. Report the new version number returned.

---

## Content Rules

- Submit **full page content** — these tools replace, not patch.
- Max 50,000 characters per submission.
- Cannot edit reserved pages: `_index`, `_log`.
- Preserve existing wikilinks `[[slug]]` unless intentionally removing them.
- Keep the page's existing frontmatter fields (title, type, knowledge_type_slugs, etc.) unless the change specifically needs to update them.

---

## When NOT to edit

- Do not edit without user instruction — even if you spot an error while querying.
- Do not create new pages via these tools (they only update existing pages).
- If the target slug doesn't exist, tell the user — new page creation is an admin/pipeline operation.
