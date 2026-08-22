from __future__ import annotations

from dataclasses import dataclass

from app.trademark_factory.store_bundle import NativeStoreBundle
from app.trademark_framework.acquisition import SourceAcquisitionAdapter
from app.trademark_framework.contracts import CountryPack
from app.trademark_framework.runtime import (
    RuntimeAdapterRegistry,
    RuntimeSourceKey,
    SourceRuntimeAdapter,
)


JURISDICTION_PLUGIN_VERSION = "TRADEMARK_JURISDICTION_PLUGIN_V1"


@dataclass(frozen=True, slots=True)
class SourceAcquisitionBinding:
    source_id: str
    adapter: SourceAcquisitionAdapter

    def validate(self, pack: CountryPack) -> tuple[str, ...]:
        errors: list[str] = []
        source_id = self.source_id.strip()
        if not source_id:
            errors.append("plugin acquisition source_id must not be blank")
            return tuple(errors)
        try:
            pack.source(source_id)
        except ValueError:
            errors.append(f"plugin acquisition source not declared by CountryPack: {source_id}")
        if not self.adapter.adapter_id.strip():
            errors.append(f"plugin acquisition adapter_id must not be blank: {source_id}")
        return tuple(errors)


@dataclass(frozen=True, slots=True)
class JurisdictionPlugin:
    """One jurisdiction's reusable registration unit.

    The plugin binds an authoritative ``CountryPack`` to optional runtime, acquisition and
    native-store implementations. It does not auto-discover modules, execute network calls, install
    schemas or promote maturity. A generated/early country may therefore register only its disabled
    CountryPack and source-only runtime skeleton while implementation evidence is still incomplete.
    """

    plugin_id: str
    pack: CountryPack
    runtime_adapters: tuple[SourceRuntimeAdapter, ...] = ()
    acquisition_bindings: tuple[SourceAcquisitionBinding, ...] = ()
    store_bundles: tuple[NativeStoreBundle, ...] = ()

    def validate(self) -> tuple[str, ...]:
        errors = list(self.pack.validate())
        plugin_id = self.plugin_id.strip()
        if not plugin_id:
            errors.append("jurisdiction plugin_id must not be blank")

        canonical = self.pack.jurisdiction.strip().upper()
        aliases = {alias.strip().upper() for alias in self.pack.aliases}
        accepted_jurisdictions = {canonical, *aliases}
        declared_sources = {source.source_id for source in self.pack.sources}

        runtime_ids: set[str] = set()
        runtime_keys: set[tuple[str, str]] = set()
        for adapter in self.runtime_adapters:
            adapter_id = adapter.adapter_id.strip()
            if not adapter_id:
                errors.append("plugin runtime adapter_id must not be blank")
            elif adapter_id in runtime_ids:
                errors.append(f"duplicate plugin runtime adapter_id: {adapter_id}")
            runtime_ids.add(adapter_id)
            if not adapter.source_keys:
                errors.append(f"{adapter_id}: plugin runtime adapter requires a source key")
            for key in adapter.source_keys:
                normalized = key.normalized()
                if normalized.jurisdiction not in accepted_jurisdictions:
                    errors.append(
                        f"{adapter_id}: runtime jurisdiction {normalized.jurisdiction} "
                        f"does not belong to plugin {canonical}"
                    )
                if normalized.source_id not in declared_sources:
                    errors.append(
                        f"{adapter_id}: runtime source not declared by CountryPack: "
                        f"{normalized.source_id}"
                    )
                canonical_key = (canonical, normalized.source_id)
                if canonical_key in runtime_keys:
                    errors.append(
                        "duplicate plugin runtime source key: "
                        f"{canonical_key[0]}:{canonical_key[1]}"
                    )
                runtime_keys.add(canonical_key)

        acquisition_sources: set[str] = set()
        acquisition_ids: set[str] = set()
        for binding in self.acquisition_bindings:
            errors.extend(binding.validate(self.pack))
            source_id = binding.source_id.strip()
            if source_id in acquisition_sources:
                errors.append(f"duplicate plugin acquisition source: {source_id}")
            acquisition_sources.add(source_id)
            adapter_id = binding.adapter.adapter_id.strip()
            if adapter_id and adapter_id in acquisition_ids:
                errors.append(f"duplicate plugin acquisition adapter_id: {adapter_id}")
            acquisition_ids.add(adapter_id)

        bundle_sources: set[str] = set()
        for bundle in self.store_bundles:
            errors.extend(bundle.validate())
            source_id = bundle.source_id.strip()
            if bundle.jurisdiction.strip().upper() != canonical:
                errors.append(
                    "plugin native store bundle jurisdiction mismatch: "
                    f"{bundle.jurisdiction!r} != {canonical!r}"
                )
            if bundle.store_schema.strip() != self.pack.store_schema.strip():
                errors.append(
                    "plugin native store bundle schema mismatch: "
                    f"{bundle.store_schema!r} != {self.pack.store_schema!r}"
                )
            if source_id not in declared_sources:
                errors.append(f"plugin native store source not declared by CountryPack: {source_id}")
            if source_id in bundle_sources:
                errors.append(f"duplicate plugin native store bundle source: {source_id}")
            bundle_sources.add(source_id)

        return tuple(errors)


@dataclass(frozen=True, slots=True)
class PluginRegistryAudit:
    version: str
    plugin_count: int
    country_count: int
    runtime_adapter_count: int
    acquisition_adapter_count: int
    native_store_bundle_count: int
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "plugin_count": self.plugin_count,
            "country_count": self.country_count,
            "runtime_adapter_count": self.runtime_adapter_count,
            "acquisition_adapter_count": self.acquisition_adapter_count,
            "native_store_bundle_count": self.native_store_bundle_count,
            "ready": self.ready,
            "errors": list(self.errors),
        }


class JurisdictionPluginRegistry:
    """Validated, side-effect-free registry for jurisdiction implementation plugins."""

    def __init__(self, plugins: tuple[JurisdictionPlugin, ...]) -> None:
        self._plugins = plugins
        self._by_jurisdiction: dict[str, JurisdictionPlugin] = {}
        self._by_acquisition_source: dict[tuple[str, str], SourceAcquisitionAdapter] = {}
        self._by_store_source: dict[tuple[str, str], NativeStoreBundle] = {}
        self._runtime_adapters: list[SourceRuntimeAdapter] = []
        errors: list[str] = []

        plugin_ids: set[str] = set()
        runtime_ids: set[str] = set()
        for plugin in plugins:
            errors.extend(plugin.validate())
            plugin_id = plugin.plugin_id.strip()
            if plugin_id in plugin_ids:
                errors.append(f"duplicate jurisdiction plugin_id: {plugin_id}")
            plugin_ids.add(plugin_id)

            canonical = plugin.pack.jurisdiction.strip().upper()
            for key in (canonical, *plugin.pack.aliases):
                normalized = key.strip().upper()
                existing = self._by_jurisdiction.get(normalized)
                if existing is not None and existing is not plugin:
                    errors.append(
                        f"jurisdiction plugin alias collision: {normalized} -> "
                        f"{existing.pack.jurisdiction}/{canonical}"
                    )
                self._by_jurisdiction[normalized] = plugin

            for adapter in plugin.runtime_adapters:
                adapter_id = adapter.adapter_id.strip()
                if adapter_id in runtime_ids:
                    errors.append(f"cross-plugin runtime adapter_id collision: {adapter_id}")
                runtime_ids.add(adapter_id)
                self._runtime_adapters.append(adapter)

            for binding in plugin.acquisition_bindings:
                source_key = (canonical, binding.source_id.strip())
                if source_key in self._by_acquisition_source:
                    errors.append(
                        "cross-plugin acquisition source collision: "
                        f"{source_key[0]}:{source_key[1]}"
                    )
                self._by_acquisition_source[source_key] = binding.adapter

            for bundle in plugin.store_bundles:
                source_key = (canonical, bundle.source_id.strip())
                if source_key in self._by_store_source:
                    errors.append(
                        "cross-plugin native store source collision: "
                        f"{source_key[0]}:{source_key[1]}"
                    )
                self._by_store_source[source_key] = bundle

        runtime_registry = RuntimeAdapterRegistry(tuple(self._runtime_adapters))
        errors.extend(runtime_registry.audit().errors)
        self._runtime_registry = runtime_registry
        self._audit = PluginRegistryAudit(
            version=JURISDICTION_PLUGIN_VERSION,
            plugin_count=len(plugins),
            country_count=len({plugin.pack.jurisdiction for plugin in plugins}),
            runtime_adapter_count=len(self._runtime_adapters),
            acquisition_adapter_count=len(self._by_acquisition_source),
            native_store_bundle_count=len(self._by_store_source),
            errors=tuple(errors),
        )

    def audit(self) -> PluginRegistryAudit:
        return self._audit

    def plugins(self) -> tuple[JurisdictionPlugin, ...]:
        return self._plugins

    def plugin(self, jurisdiction: str) -> JurisdictionPlugin:
        key = jurisdiction.strip().upper()
        try:
            return self._by_jurisdiction[key]
        except KeyError as exc:
            raise ValueError(f"unregistered trademark jurisdiction plugin: {jurisdiction}") from exc

    def country_pack(self, jurisdiction: str) -> CountryPack:
        return self.plugin(jurisdiction).pack

    def runtime_registry(self) -> RuntimeAdapterRegistry:
        if not self._audit.ready:
            raise RuntimeError(f"jurisdiction plugin registry is invalid: {self._audit.errors}")
        return self._runtime_registry

    def acquisition_adapter(
        self,
        jurisdiction: str,
        source_id: str,
    ) -> SourceAcquisitionAdapter:
        plugin = self.plugin(jurisdiction)
        key = (plugin.pack.jurisdiction, source_id.strip())
        try:
            return self._by_acquisition_source[key]
        except KeyError as exc:
            raise ValueError(
                f"no acquisition adapter in plugin for source: {key[0]}:{key[1]}"
            ) from exc

    def native_store_bundle(self, jurisdiction: str, source_id: str) -> NativeStoreBundle:
        plugin = self.plugin(jurisdiction)
        key = (plugin.pack.jurisdiction, source_id.strip())
        try:
            return self._by_store_source[key]
        except KeyError as exc:
            raise ValueError(
                f"no native store bundle in plugin for source: {key[0]}:{key[1]}"
            ) from exc


__all__ = [
    "JURISDICTION_PLUGIN_VERSION",
    "JurisdictionPlugin",
    "JurisdictionPluginRegistry",
    "PluginRegistryAudit",
    "SourceAcquisitionBinding",
    "RuntimeSourceKey",
]
