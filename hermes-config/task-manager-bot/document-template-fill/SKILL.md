---
name: document-template-fill
description: "Fill a blank document template using data from a second uploaded file."
version: 1.0.0
license: Proprietary
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pm-chaser, docx, template, document]
---

# Document Template Fill

## When to Use

Two files uploaded together where one is a fillable template (a form, an order/invoice layout, anything with blank fields waiting to be filled) and the other is data to fill it with. This is not about pm-chaser tasks at all — nothing gets written to the database, the output is a finished document handed back.

## Procedure

1. Read the template's own structure to learn what fields it actually has — never assume a fixed field list, since a personalised template swapped in later will have entirely different fields than whatever template was used last.
2. Read the source next: it may be tabular/labeled (a delivery note, a form someone half-filled) or genuinely unstructured free text (a WhatsApp-style message, a sentence someone typed) — map its content onto the template's fields by understanding both documents, not by assuming fixed line/column positions, since a free-text source has none.
3. If it's unclear which of the two uploaded files is the template and which is the source (no obvious blank-fields-vs-filled-content distinction), ask rather than guess.
4. Every field on the template gets a fate: resolved (with the value found in the source, and where it came from) or unresolved (nothing in the source addresses it — leave it blank on the output, never invent a value). A genuinely ambiguous source (e.g., only one date given when the template has two separate date fields) is also unresolved, not a coin-flip guess — ask which field it belongs to. A repeated/duplicate entry in the source (the same line item listed twice) gets flagged too, not silently included twice.
5. Show ONE consolidated preview — every field and what it'll be filled with, everything left unresolved and why, any duplicates flagged — wait for one explicit yes (or corrections) before touching the actual file. A wrong fill is a real document handed to someone, not a draft.
6. Only then produce the real filled file, following the `docx` skill's edit-an-existing-document path (unzip → **run `scripts/merge_runs.py` on it and confirm it actually succeeded** → edit `word/document.xml` → rezip → validate) rather than building one from scratch. **The merge step is not optional and not skippable on a path error** — go find the real path (it's under the `docx` skill's own directory, not the document's cache folder) and get it running, because text-matching against unmerged XML silently fails to find labels Word split across multiple runs, which fills nothing and looks like success. A source with more line items than the template has table rows means duplicating the row's XML structure per item AND removing the template's own original placeholder row — a leftover blank row alongside the real ones is a visible defect, not a harmless extra.
7. **Render and visually check the result before sending it — this is a hard gate, not a should.** Convert to PDF, then image, then actually look at the image. If LibreOffice or any part of that chain is unavailable and you cannot complete this step, **you do not have a verified result** — say so plainly to the manager and do not send the file, the same way you'd never claim a message sent without a real `checkin_id` to point to. A confident-sounding summary of what was filled is not evidence it was actually filled — the field-mapping preview earlier in this rule was your *intent*; this step is the only thing that confirms the intent actually landed in the file. Real failure mode this guards against: every field can be individually claimed as filled in your closing summary while several of them are actually blank in the document, because the XML edit silently no-opped — the render is what catches that, nothing else will.
8. Send the finished file back with a `MEDIA:/path/to/file` tag in your reply — that's what makes the gateway deliver it as a real Telegram attachment. Naming the path in prose instead does not send anything.

## Pitfalls

- Assuming the last template's field list applies to a new one instead of reading each template fresh — a swapped-in personalised template will have different fields.
- Guessing a value for a field the source never actually addressed instead of leaving it blank and saying so in the preview.
- Skipping `merge_runs.py` because of a path error and text-matching the unmerged XML anyway — a real incident, and it silently left "Deliver To," "Delivery Address," and both date fields blank while the closing summary confidently claimed they were filled.
- Leaving the template's original placeholder row in the table alongside the newly added ones instead of removing it.
- Sending the filled file back by naming its path in prose instead of a `MEDIA:` tag.
- Sending the file when the render-and-look verify step couldn't be completed instead of reporting that plainly — a summary of intended edits is not proof they landed.

## Worked example

> Manager: *uploads Delivery_Order_Template.docx (blank fields: company/address, invoice-DO no., issue date, customer P.O., deliver-to, delivery address, a line-items table, date/time of delivery, remarks) together with a short note: "10 July 2025, Lakeside residence, Marcus Tan, 91234567, then A-001 apple 10, A-003 pear 12, A-005 orange 16, A-001 apple 10"*
> You: *read the template's real fields; read the source text as free-form, not fixed columns*
> You: "Here's what I can fill in, boss — flag anything before I generate it:
> - Deliver To: Marcus Tan (91234567)
> - Delivery Address: Lakeside residence
> - Line items: A-001 apple ×10, A-003 pear ×12, A-005 orange ×16 — note A-001 apple ×10 appears twice in your message, I've only included it once, let me know if that second one was meant to be different
> - Company name/address, Invoice/DO No., and Customer P.O.: nothing in your message covers these, leaving blank
> - Only one date given (10 July 2025) but the template has both an Issue Date and a Date of Delivery — which one is it, or are they the same?
> Want me to generate it with these as-is once you confirm the date?"
> Manager: "same date for both, leave the rest as you have it"
> You: *fill the template per the docx skill's edit-existing-document path, add table rows for all 3 line items, render to PDF/image and check it, then reply with the file*
> You: "Done — filled delivery order attached. Company info, DO number, and P.O. are blank since nothing covered them." *[MEDIA:/path/to/filled_Delivery_Order.docx]*
