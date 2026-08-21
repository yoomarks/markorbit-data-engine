# Global Trademark Public Source Research — 2026-08

This note records source-discovery findings only. It does **not** enable acquisition, create country schemas, or assert production readiness.

Architecture rule: each jurisdiction should retain a country-native, source-faithful store. Cross-jurisdiction projection is downstream and must not force sources into a lowest-common-denominator schema.

## Recommended next jurisdictions

### Tier A — strong candidates for country-native stores

#### France (FR) — INPI

Official DATA INPI exposes French trade mark data from 1976 onward. INPI also offers free API/SFTP access after account setup. The SFTP trademark delivery is especially attractive for Data Engine:

- French trade marks, active and inactive, since 1976
- bibliographic, administrative and legal data
- logo/word-mark images since 1982
- MP3/MP4 for sound/video marks
- estimated stock around 3 million marks / 15 GB
- weekly Friday updates around 60 MB
- XML using WIPO ST.66 plus image/media assets

Candidate source IDs:

- `INPI_FR_ST66_STOCK`
- `INPI_FR_ST66_WEEKLY`
- `INPI_FR_API`

Recommended role: stock + weekly SFTP as PRIMARY/INCREMENTAL; API as ENRICHMENT/verification.

Official references:
- https://data.inpi.fr/content/editorial/lien-serveur-ftp-PI
- https://data.inpi.fr/content/editorial/apis_pi

#### Japan (JP) — JPO / INPIT

JPO's Patent Information Standardized Data explicitly includes patents, utility models, designs **and trademarks**, with bibliographic/history data grouped by period. Current delivery is TSV, published on JPO business days and also bundled weekly. JPO/INPIT also operate a bulk download service; latest published data are downloadable, while archived datasets can be requested. The download service itself is free, subject to registration/terms.

Important trademark-relevant masters include application and registration masters; registration data covers rights-related transfer, extinction and changes. Appeals moved to a newer sharedDB format in 2025 and have daily/weekly differential delivery.

Candidate source IDs:

- `JPO_STANDARDIZED_INITIAL`
- `JPO_STANDARDIZED_DAILY`
- `JPO_STANDARDIZED_WEEKLY`
- `JPO_SHAREDDB_APPEAL`

Recommended role: official bulk PRIMARY + incremental. Build a JP-native relational model rather than treating gazettes as the main registry.

Official references:
- https://www.jpo.go.jp/e/system/laws/koho/internet/standardized-data.html
- https://www.jpo.go.jp/e/system/laws/koho/internet/download.html

#### Korea (KR) — KIPRIS Plus

KIPRIS Plus provides both Bulk Data and REST Open API products for Korean IP. Trademark products include bulk trademark records, trademark bulletins, administrative procedures, classification-change history, legal-status history and related registration/trial data. Some bulk products are paid; KIPRIS Plus also publishes a free-data catalogue, so licensing/cost must be evaluated per product before implementation.

The system supports long historical coverage for several trademark-administration products (some from 1950 to current) and uses structured TXT/XML/ST.96 depending on product.

Candidate source IDs:

- `KIPRISPLUS_KR_TRADEMARK_BULK`
- `KIPRISPLUS_KR_TRADEMARK_BULLETIN`
- `KIPRISPLUS_KR_ADMIN_HISTORY`
- `KIPRISPLUS_KR_LEGAL_STATUS`
- `KIPRISPLUS_KR_API`

Recommended role: evaluate the free/paid product split first; if terms and cost are acceptable, KR is a high-value country-native store.

Official references:
- https://plus.kipris.or.kr/eng/main.do
- https://plus.kipris.or.kr/eng/data/clas/List.do?menuNo=310103

#### Switzerland (CH) — IPI / Swissreg

The Swiss Federal Institute of Intellectual Property offers trademark data delivery via API free of charge after signing terms of use. Swissreg itself contains active and cancelled marks and applications, current status and history, goods/services, owner addresses, representative data where applicable, and official publications such as owner changes and renewals.

Candidate source IDs:

- `IPI_CH_TRADEMARK_API`
- `SWISSREG_PUBLICATION_FEED` (only if API/publication semantics support incremental discovery)

Recommended role: API-based PRIMARY/INCREMENTAL candidate. Before implementation verify API pagination, initial-backfill mechanics, rate limits, image/document endpoints and terms.

Official references:
- https://www.ige.ch/en/services/digital-resources/ip-data/data-delivery-api
- https://www.ige.ch/en/services/digital-resources/databases-and-directories/swissreg/trade-mark-database

### Tier B — useful public datasets, but not obviously a complete live registry feed

#### Brazil (BR) — INPI / BADEPI

Brazilian INPI publishes open trademark microdata through BADEPI. Current BADEPI v11.0 covers a historical series from 2000 through 2024 and is updated annually. This is useful as an official historical seed and for statistical/portfolio discovery, but should not automatically be treated as a current registry replica.

There are also older open ZIPs for trademark applications (for example 2018–2020), but BADEPI is the cleaner current research target.

Candidate source IDs:

- `BR_INPI_BADEPI_TRADEMARK`
- `BR_INPI_RPI` (future investigation for incremental legal/publication events)

Recommended role: HISTORICAL_SEED until a reliable official current-state/incremental source is confirmed.

Official references:
- https://www.gov.br/inpi/pt-br/inpi-data/dados-e-series-temporais/badepi
- https://www.gov.br/inpi/pt-br/acesso-a-informacao/dados-abertos

#### Spain (ES) — OEPM BOPI XML

OEPM publishes the Official Industrial Property Bulletin and provides publication downloads by date. Trademark/sign publications are available in the trademark volume; XML and XSD infrastructure exists and was updated in March 2026. This is promising as an event/publication stream, but this research pass did not confirm an unrestricted full registry stock download comparable to CIPO or INPI France.

Candidate source IDs:

- `OEPM_BOPI_TRADEMARK_XML`
- `OEPM_REGISTRY` (placeholder only; do not mark ready until a stock/API source is verified)

Recommended role: INCREMENTAL publication/event source after terms/access are verified; do not claim full current-state coverage from BOPI alone.

Official references:
- https://consultas2.oepm.es/bopiweb/descargaPublicaciones/formBusqueda.action
- https://www.oepm.es/es/detalle-noticia/Actualizacion-de-los-archivos-XML-y-los-esquemas-XSD-del-Boletin-Oficial-de-la-Propiedad-Industrial-00001/

### Tier C — public search/data exists, but no strong bulk-registry path confirmed yet

#### Germany (DE) — DPMAregister

DPMAregister has rich official national trademark data, daily register updates and weekly trademark-journal updates. Current registered marks can reach back to filing dates in 1875; applications are broadly covered from 1998, with known historical limitations. This research pass did not yet confirm a free public bulk/API delivery channel equivalent to France/Japan/Canada.

Recommended next research: DPMA data-delivery/licensing channels, machine-readable journal endpoints, and whether register exports can support deterministic incremental ingestion.

Official reference:
- https://register.dpma.de/register/htdocs/test/en/hilfe/datenbestand/marken/index.html

#### Mexico (MX) — IMPI

IMPI has a public open-data portal and monthly lists/statistics for distinctive signs. The currently obvious public downloads are statistical datasets and recent application listings, rather than a confirmed complete historical/current registry dump.

Recommended role: research further before schema implementation.

Official reference:
- https://datosabiertos.impi.gob.mx/

#### India (IN) — IP India

IP India provides official trademark public search, trademark journals and dashboards. The new public search currently requires OTP/captcha interaction. This research pass did not identify an official bulk registry dataset or machine API suitable for unattended full backfill.

Recommended role: no ingestion implementation yet; monitor for official bulk/API releases and investigate journal acquisition separately.

Official references:
- https://ipindia.gov.in/pages/e-services
- https://tmrsearch.ipindia.gov.in/ESEARCH

## Priority recommendation

When CN capacity becomes available, source onboarding priority should be:

1. France — unusually strong combination of full stock, weekly differential delivery, legal/admin fields and assets.
2. Japan — mature official bulk model with daily/weekly delivery and deep history.
3. Switzerland — free official trademark API, likely smaller and operationally manageable.
4. Korea — very rich but requires per-product cost/terms analysis before committing.
5. Brazil — useful official historical seed, but current-state strategy still needed.
6. Spain — useful bulletin/event stream; stock/current-state source still unresolved.

Germany, Mexico and India remain research candidates rather than implementation commitments.

## Data Engine guardrails

- `source_available` and `pipeline_ready` remain separate states.
- No source discovered here should be added to active acquisition until terms, access method, initial-backfill capability and incremental semantics are verified.
- Registry/source deletion must never automatically become a legal-status conclusion.
- Preserve source-native country schemas; use global projection only as an indexing/query layer.
- Prefer stock + differential/event architecture where the office supports it.
