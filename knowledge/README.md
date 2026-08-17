# knowledge/

RAG source markdown for the DosaDash assistant. Ingested into pgvector
(hybrid BM25 + vector RRF); edits here trigger re-embedding via the event
cascade (Hard Rule 4).

## Layout

```
allergens.md      GENERATED allergen & dietary guide — do not edit by hand.
                  Regenerate: python -m dosadash_ml.datagen.knowledge
                  (test_knowledge_sync.py fails CI if it drifts from menu.py)
menu-guide/       Editorial per-category dish guides (pairings, spice, travel)
faq.md            Customer FAQ (ordering, delivery, payments, Telegram)
policies.md       Operative policies (cancellation, refunds, hours, allergy)
```

## Authoring rules

- Every file starts with YAML front-matter:

  ```yaml
  ---
  title: Human-readable title
  doc_type: allergen_guide | menu_guide | faq | policy
  tags: [lowercase, keywords]
  ---
  ```

- Chunking is heading-based (`##`/`###`): keep each section self-contained —
  it must make sense when retrieved alone, with the dish/topic named in the
  heading or first line.
- State only facts the platform enforces (order states, pincodes, GST, test
  mode). If a fact lives in the DB or admin settings and can change at
  runtime (hours, item availability, prices beyond indicative ₹), describe
  the mechanism, don't hard-code the value.
- Menu facts (allergens, diet flags, spice) belong in the generated
  `allergens.md` — editorial files may repeat them for readability but the
  generated table is the citable source.
