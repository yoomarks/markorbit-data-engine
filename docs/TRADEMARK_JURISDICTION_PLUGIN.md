# Trademark Jurisdiction Plugin

`TRADEMARK_JURISDICTION_PLUGIN_V1` is the reusable registration boundary for one jurisdiction's implementation. It sits above the existing CountryPack/runtime/acquisition/native-store contracts and does not replace them.

A `JurisdictionPlugin` may bind:

- one authoritative `CountryPack`;
- zero or more source runtime adapters;
- zero or more source acquisition adapters;
- zero or more reviewed `NativeStoreBundle` definitions.

The plugin is deliberately side-effect free. Constructing or auditing it does not make network calls, install DDL, ingest data, change a source's `pipeline_ready` flag, advance jurisdiction maturity, or establish current/legal status.

## Why this exists

Before the plugin boundary, adding a country still implied edits to central runtime/registration modules even after acquisition, mapping and native-store mechanics had become reusable. The plugin contract makes the jurisdiction implementation itself portable: a source-specific module can package the CountryPack and the implementation objects that belong to it, then a registry can validate and expose them through generic source entrypoints.

`TRADEMARK_RUNTIME_ADAPTER_V2` supports this model by allowing source-only adapters. A new jurisdiction using generic `ingest-source` no longer needs a bespoke top-level CLI command merely to appear in the runtime registry. At least one exact runtime source key is still required.

## Validation

The plugin and plugin registry fail closed on structural drift, including:

- blank/duplicate plugin or runtime adapter ids;
- runtime source keys outside the plugin jurisdiction/aliases;
- runtime, acquisition or native-store sources absent from the CountryPack;
- duplicate runtime/acquisition/native-store source bindings;
- native-store bundle jurisdiction or store-schema mismatch;
- invalid native-store bundle definitions;
- cross-plugin jurisdiction/alias or runtime-adapter collisions.

The registry can produce a regular `RuntimeAdapterRegistry`, resolve acquisition adapters by canonical jurisdiction/source, and resolve native-store bundles. Alias resolution happens through the plugin's CountryPack rather than source-specific conditionals.

## Source-only runtime example

A plugin runtime can expose:

```text
commands = ()
source_keys = ((JP, JPO_OFFICIAL),)
```

and still participate in the generic runtime registry. This is intentional: scalable country onboarding should use source registration plus generic dispatch, not create `ingest-jp`, `ingest-kr`, `ingest-sg`, and similar command branches for every jurisdiction.

## Boundaries

The V1 plugin registry is explicit; it does not scan/import arbitrary filesystem modules automatically. Automatic discovery is deferred until module trust, deterministic import ordering and deployment packaging rules are proven. Existing US/GB/EU/CA/AU/NZ registries remain compatible and are not migrated in this change.

A plugin is engineering registration evidence only. It is not release acceptance, jurisdiction-current acceptance, trusted-for-silence evidence, or a legal conclusion.
