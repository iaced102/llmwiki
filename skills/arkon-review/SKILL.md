---
name: arkon-review
description: "Review, approve, or reject pending wiki edit drafts in Arkon. Requires editor or admin role. Triggers on: review drafts, pending reviews, check draft queue, approve draft, reject draft, review wiki changes."
allowed-tools: mcp__arkon__list_pending_drafts mcp__arkon__review_draft mcp__arkon__approve_draft mcp__arkon__reject_draft mcp__arkon__read_wiki_page
---

# arkon-review: Review Wiki Drafts

Editor/admin only. If `list_pending_drafts` returns a permission error, you don't have the required role.

---

## Review Workflow

### 1. List pending drafts

```
list_pending_drafts()
```

Returns: draft ID, page slug, author, timestamp, note. Filter by workspace if needed:
```
list_pending_drafts(workspace_id="<uuid>")
```

### 2. Read a draft for review

```
review_draft(draft_id)
```

Returns side-by-side: **proposed content** and **current page content (vN)**. Read both carefully.

You may also call `read_wiki_page(slug)` independently for fuller backlink context.

### 3. Decide

**Approve as-is:**
```
approve_draft(draft_id, reviewer_note="optional feedback to author")
```

**Approve with your own edits** (you want to tweak before publishing):
```
approve_draft(draft_id, edited_content_md="...", reviewer_note="approved with minor edits")
```

**Reject:**
```
reject_draft(draft_id, reviewer_note="clear reason why — required")
```

`reviewer_note` is **required** for rejections — the author needs to understand what to fix.

---

## Review Checklist

Before approving, verify:

- [ ] Content is factually consistent with other wiki pages (check backlinks if unsure)
- [ ] No sensitive data (PII, credentials, confidential figures) exposed
- [ ] Wikilinks `[[slug]]` point to real pages, not broken references
- [ ] Tone matches the KB style (factual, neutral, encyclopedic)
- [ ] The change note from the author makes the intent clear

---

## Batch Review

For multiple drafts:

1. `list_pending_drafts()` — get the full queue.
2. Group by page type or workspace if useful.
3. `review_draft` each one in order.
4. Approve/reject individually — do not bulk approve without reading each.

Always confirm with the user before approving a batch of more than 3 drafts in one go.

---

## Notes

- Approving a draft writes directly to the wiki page and creates a revision in history.
- Rejected drafts stay in the system with `status: rejected` — they are not deleted.
- You cannot approve your own drafts (the server enforces this).
