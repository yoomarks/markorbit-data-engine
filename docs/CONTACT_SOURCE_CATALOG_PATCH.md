# Contact Source Catalog Patch

`文件清单.xlsx` is treated as reviewed source evidence for existing Contacts imports.
The curated catalog distinguishes source segment (`AGENT` / `DIRECT`), source scope
(country, `综合`, ARIPO, OAPI, EU), and an optional ISO country fallback.

## Country precedence

1. Existing row/entity country is authoritative and is never overwritten.
2. If an entity country is empty, a reviewed source default may fill it.
3. A source fallback is applied only when all country-bearing reviewed sources linked
   to that entity agree on exactly one country.
4. `综合`, ARIPO, OAPI and EU source scopes do not invent a country.
5. Person country is filled only when missing and all linked entity countries agree.

## Apply

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\patch-contact-source-catalog.ps1
```

The patch is idempotent. It adds these additive columns to `contact.source` if needed:

- `source_segment`
- `source_scope`
- `default_country_code`

It then updates matching imported sources and fills only missing, unambiguous entity/person
country values. It does not change CN/US trademark fact tables, replay order, or mutation gates.
