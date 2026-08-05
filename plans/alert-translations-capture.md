---
status: in-progress
depends: [entity-type-capture]
specs: []
issues: [98]
---

# Alert translations: full-fidelity JSON capture (#98 option c)

The service_alerts keep-first columns store `translation[0].text` with the
language dropped. The 2026-08-04 language census (938 alerts) showed that
loses whole languages (511.org publishes en/es/pa/vi/zh) and keeps the
WRONG one for AC Transit (its `translation[0]` is Spanish, 22/22 sampled) —
"first" is producer whim, unordered by spec.

User decision 2026-08-05: **option (c) alone** — capture everything as
JSON alongside the untouched keep-first columns. Options (a) language
columns and (b) prefer-English selection are deliberately NOT adopted:
both are derivable downstream from (c), and (b) would bake a
display-selection policy into compaction (the exact
schema-frozen-at-compaction pattern the grain-decision DESIGN.md entry
warns about) while forcing a second license_plate-style semantic
migration. Ships as its own PR stacked on #99 (reuses its
`_translations_json` helper; targets the v0.9.3 release as pass three).

## Scope

- service_alerts schema + extractor + BigQuery DDL: 9 new columns — 8
  `*_translations_json` (header_text, description_text, url,
  tts_header_text, tts_description_text, cause_detail, effect_detail,
  image_alternative_text) + `image_localized_images_json`
  (`[{"url","media_type","language"},…]`; url/media_type proto2-required,
  language per-field presence)
- JSON conventions inherited: NULL when unset (never `"[]"`), publisher
  order, compact separators
- Manifest: the eight `.translation.language` DROPs and the
  `localized_image.language` DROP flip to captures; `.text` leaves feed
  both compat column and JSON
- Keep-first columns untouched; DESIGN.md re-documents them as "first
  translation AS PUBLISHED"
- Bindings guard: `LocalizedImage.language`

## Validation criteria

- [ ] Manifest: zero remaining keep-first DROP entries; reverse coverage
      green for the 9 new columns
- [ ] Behavior test: AC Transit case (es first, en second) keeps both;
      en-html variant preserved; untagged translation → language null;
      unset fields → NULL never "[]"
- [ ] Populated round-trip covers all 9 columns; record↔schema and DDL
      parity green
- [ ] ruff + mypy strict + full pytest green
- [ ] Real-data spot check: 511.org (5 languages) and AC Transit alerts
      through the extractor
- [ ] Targeted `tofu plan`: service_alerts update in-place, 0 destroy

## Risks / unknowns

- Row width: the 9 JSON strings replicate onto every informed-entity row,
  like `active_periods_json` — bounded to alert text, noise next to VP/TU
  volume (the #91 "doubles text storage" concern was accepted then for
  less benefit).
- Pre-#98 partitions lack the columns (read NULL via #86 mixed-schema
  contract); keep-first columns remain the only translation signal there.
