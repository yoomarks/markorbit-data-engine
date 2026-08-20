# CN Mark Image Bulk V1

`CN_MARK_IMAGE_BULK_V1` treats CN official mark-image ZIP files as transport packages, not permanent assets.

## Storage contract

- Official source images are JPEG.
- Raw images are stored by SHA-256 content address; trademark numbers never appear in physical storage keys.
- Duplicate package observations reuse the existing raw asset.
- Canonical JPEG derivatives are stored separately so a large HDD can hold raw evidence while an SSD holds processed visual assets.
- Small or low-resolution official JPEGs pass through without needless recompression.
- Large or oversized JPEGs are downsampled/re-encoded by `CN_MARK_IMAGE_NORMALIZATION_V1`.
- Package/entry lineage, raw SHA, visual versions, and current trademark-to-asset bindings remain in PostgreSQL.
- ZIP deletion is allowed only after package acceptance and is explicit with `--delete-source-on-acceptance`.

## Roots

`VISUAL_RAW_ROOT` is the detachable raw visual store. `VISUAL_PROCESSED_ROOT` is the processed/canonical visual store. If unset, raw assets remain under `RAW_DATA_ROOT` and processed assets under `RAW_DATA_ROOT/visual_processed`.

The intended workstation layout is compatible with a large HDD for raw assets and an SSD for processed visual assets without embedding drive letters in database keys.

## Import

```bash
python -m app.cn_mark_image.importer /path/to/package.zip \
  --package-kind HISTORICAL \
  --source-rank 20200101 \
  --delete-source-on-acceptance
```

`source-rank` must follow official package chronology. Filename-to-application-number inference intentionally fails closed: V1 maps only a basename made entirely of digits. Other filename contracts remain imported and traceable but are left unmapped until verified from real source packages.
