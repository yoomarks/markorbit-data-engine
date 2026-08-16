from __future__ import annotations

from dataclasses import dataclass

from app.work_dag import WorkDagDefinition, WorkDagNode


CN_FINAL_PUBLISH_DAG_VERSION = "CN_FINAL_PUBLISH_DAG_V1"


@dataclass(frozen=True)
class LegacyPublishRule:
    task_id: str
    source_tables: tuple[str, ...]
    required_markers: tuple[str, ...]
    forbidden_markers: tuple[str, ...] = ()

    def matches(self, sql: str) -> bool:
        return (
            all(f"markorbit_facts.{table}" in sql for table in self.source_tables)
            and all(marker in sql for marker in self.required_markers)
            and not any(marker in sql for marker in self.forbidden_markers)
        )


CN_FINAL_PUBLISH_DAG = WorkDagDefinition(
    dag_id="CN_FINAL_PUBLISH",
    version=CN_FINAL_PUBLISH_DAG_VERSION,
    nodes=(
        WorkDagNode(
            "CASE_FACTS_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "PRELIMINARY_PUBLICATION_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("CASE_FACTS_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "REGISTRATION_PUBLICATION_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("PRELIMINARY_PUBLICATION_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "EXCLUSIVE_TERM_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("REGISTRATION_PUBLICATION_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "MARK_NAME_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("EXCLUSIVE_TERM_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "AGENT_CODE_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("MARK_NAME_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "GOODS_SCOPE_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("AGENT_CODE_EVENT",),
            stage_table="cn_stage_scope_publish",
            audit_policy="NATIVE_DURABLE_RANGE_DELTA_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "PARTY_SUPERSEDED_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("GOODS_SCOPE_EVENT",),
            stage_table="cn_stage_party_publish",
            audit_policy="NATIVE_DURABLE_RANGE_PARTY_REPLACEMENT_EVENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "PARTY_OBSERVED_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("PARTY_SUPERSEDED_EVENT",),
            stage_table="cn_stage_party_publish",
            audit_policy="EVENT_DELTA_ADAPTER_V2",
        ),
        WorkDagNode(
            "PARTY_HISTORY_SUPERSEDED",
            "PUBLISH_HISTORY_COMPAT",
            "cn_case_party_relation_history",
            "APPLICATION_RANGE",
            dependencies=("PARTY_OBSERVED_EVENT",),
            stage_table="cn_stage_party_publish",
            audit_policy="LEGACY_COMPATIBILITY_SINK_MAY_BE_SUPPRESSED",
        ),
        WorkDagNode(
            "PARTY_HISTORY_OBSERVED",
            "PUBLISH_HISTORY_COMPAT",
            "cn_case_party_relation_history",
            "APPLICATION_RANGE",
            dependencies=("PARTY_HISTORY_SUPERSEDED",),
            stage_table="cn_stage_party_publish",
            audit_policy="LEGACY_COMPATIBILITY_SINK_MAY_BE_SUPPRESSED",
        ),
        WorkDagNode(
            "CASE_PARTY_CURRENT_CLOSE",
            "PUBLISH_CURRENT",
            "cn_case_party_current",
            "APPLICATION_RANGE",
            dependencies=("PARTY_OBSERVED_EVENT",),
            stage_table="cn_stage_party_publish",
            audit_policy="NATIVE_DURABLE_RANGE_CURRENT_REPLACEMENT_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "CASE_CURRENT",
            "PUBLISH_CURRENT",
            "cn_case_current",
            "APPLICATION_RANGE",
            dependencies=("AGENT_CODE_EVENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_CURRENT_RANK_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "CASE_SCOPE_CURRENT",
            "PUBLISH_CURRENT",
            "cn_case_scope_current",
            "APPLICATION_RANGE",
            dependencies=("GOODS_SCOPE_EVENT", "CASE_CURRENT"),
            stage_table="cn_stage_scope_publish",
            audit_policy="NATIVE_DURABLE_RANGE_CURRENT_RANK_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "CASE_PARTY_CURRENT",
            "PUBLISH_CURRENT",
            "cn_case_party_current",
            "APPLICATION_RANGE",
            dependencies=("CASE_PARTY_CURRENT_CLOSE", "CASE_CURRENT"),
            stage_table="cn_stage_party_publish",
            audit_policy="NATIVE_DURABLE_RANGE_CURRENT_RANK_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "AGENT_CURRENT",
            "PUBLISH_CURRENT",
            "cn_agent_current",
            "AGENT_CODE_BATCH",
            dependencies=("CASE_PARTY_CURRENT",),
            stage_table="cn_stage_basic",
            audit_policy="NATIVE_DURABLE_AGENT_BATCH_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "PRIORITY_CURRENT",
            "PUBLISH_CURRENT",
            "cn_priority_current",
            "APPLICATION_RANGE",
            dependencies=("AGENT_CURRENT",),
            stage_table="cn_stage_priority",
            audit_policy="NATIVE_DURABLE_RANGE_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "MADRID_CURRENT",
            "PUBLISH_CURRENT",
            "cn_madrid_current",
            "APPLICATION_RANGE",
            dependencies=("PRIORITY_CURRENT",),
            stage_table="cn_stage_madrid",
            audit_policy="NATIVE_DURABLE_RANGE_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "CASE_RELATION_CURRENT",
            "PUBLISH_CURRENT",
            "cn_case_relation_current",
            "APPLICATION_RANGE",
            dependencies=("MADRID_CURRENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="NATIVE_DURABLE_RANGE_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
        WorkDagNode(
            "DERIVED_CASE_EVENT",
            "EMIT_EVENT",
            "cn_observed_event",
            "APPLICATION_RANGE",
            dependencies=("CASE_RELATION_CURRENT",),
            stage_table="cn_stage_case_publish",
            audit_policy="EVENT_DELTA_ADAPTER_V2",
        ),
        WorkDagNode(
            "SCOPE_CARVE_OUT_CURRENT",
            "PUBLISH_CURRENT",
            "cn_scope_carve_out_current",
            "APPLICATION_RANGE",
            dependencies=("CASE_RELATION_CURRENT", "CASE_SCOPE_CURRENT"),
            stage_table="cn_stage_scope_publish",
            audit_policy="NATIVE_DURABLE_RANGE_AND_REAL_DB_EQUIVALENCE",
            native_execution=True,
        ),
    ),
)


# The graph above is the semantic authority. These rules identify the legacy
# publisher's sequencing placeholders. Native nodes still resolve through this
# map so order drift fails closed, but their legacy SQL is not executed for new
# checkpoints carrying the node's cutover marker.
LEGACY_PUBLISH_RULES = (
    LegacyPublishRule(
        "CASE_FACTS_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "CASE_FACTS_CHANGED_OBSERVED"),
    ),
    LegacyPublishRule(
        "PRELIMINARY_PUBLICATION_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "PRELIMINARY_PUBLICATION_OBSERVED"),
    ),
    LegacyPublishRule(
        "REGISTRATION_PUBLICATION_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "REGISTRATION_PUBLICATION_OBSERVED"),
    ),
    LegacyPublishRule(
        "EXCLUSIVE_TERM_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "TERM_EXTENDED_OBSERVED"),
    ),
    LegacyPublishRule(
        "MARK_NAME_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "MARK_NAME_CHANGED_OBSERVED"),
    ),
    LegacyPublishRule(
        "AGENT_CODE_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "AGENT_CODE_CHANGED_OBSERVED"),
    ),
    LegacyPublishRule(
        "GOODS_SCOPE_EVENT",
        ("cn_stage_scope_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "GOODS_SCOPE_CHANGED_OBSERVED"),
    ),
    LegacyPublishRule(
        "PARTY_SUPERSEDED_EVENT",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "_RELATION_SUPERSEDED_OBSERVED"),
    ),
    LegacyPublishRule(
        "PARTY_OBSERVED_EVENT",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "_RELATION_OBSERVED"),
        forbidden_markers=("_RELATION_SUPERSEDED_OBSERVED",),
    ),
    LegacyPublishRule(
        "PARTY_HISTORY_SUPERSEDED",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_case_party_relation_history", "'SUPERSEDED'"),
        forbidden_markers=("'OBSERVED_CURRENT'",),
    ),
    LegacyPublishRule(
        "PARTY_HISTORY_OBSERVED",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_case_party_relation_history", "'OBSERVED_CURRENT'"),
    ),
    LegacyPublishRule(
        "CASE_PARTY_CURRENT_CLOSE",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_case_party_current", "SUPERSEDED_BY_SOURCE_OBSERVATION"),
    ),
    LegacyPublishRule(
        "CASE_CURRENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_case_current",),
    ),
    LegacyPublishRule(
        "CASE_SCOPE_CURRENT",
        ("cn_stage_scope_publish",),
        ("INSERT INTO markorbit_facts.cn_case_scope_current",),
    ),
    LegacyPublishRule(
        "CASE_PARTY_CURRENT",
        ("cn_stage_party_publish",),
        ("INSERT INTO markorbit_facts.cn_case_party_current", "'OBSERVED_CURRENT'"),
        forbidden_markers=("SUPERSEDED_BY_SOURCE_OBSERVATION",),
    ),
    LegacyPublishRule(
        "AGENT_CURRENT",
        ("cn_stage_basic", "cn_stage_agent"),
        ("INSERT INTO markorbit_facts.cn_agent_current",),
    ),
    LegacyPublishRule(
        "PRIORITY_CURRENT",
        ("cn_stage_priority",),
        ("INSERT INTO markorbit_facts.cn_priority_current",),
    ),
    LegacyPublishRule(
        "MADRID_CURRENT",
        ("cn_stage_madrid",),
        ("INSERT INTO markorbit_facts.cn_madrid_current",),
    ),
    LegacyPublishRule(
        "CASE_RELATION_CURRENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_case_relation_current",),
    ),
    LegacyPublishRule(
        "DERIVED_CASE_EVENT",
        ("cn_stage_case_publish",),
        ("INSERT INTO markorbit_facts.cn_observed_event", "DERIVED_CASE_OBSERVED"),
    ),
    LegacyPublishRule(
        "SCOPE_CARVE_OUT_CURRENT",
        ("cn_stage_scope_publish",),
        ("INSERT INTO markorbit_facts.cn_scope_carve_out_current",),
    ),
)


_KNOWN_SOURCE_TABLES = tuple(
    sorted({table for rule in LEGACY_PUBLISH_RULES for table in rule.source_tables})
)


def resolve_legacy_publish_command(sql: str) -> WorkDagNode | None:
    present = tuple(
        table for table in _KNOWN_SOURCE_TABLES if f"markorbit_facts.{table}" in sql
    )
    if not present:
        return None

    matches = [rule for rule in LEGACY_PUBLISH_RULES if rule.matches(sql)]
    if len(matches) != 1:
        task_ids = [rule.task_id for rule in matches]
        raise RuntimeError(
            "Legacy CN publish SQL does not map to exactly one explicit DAG node: "
            f"sources={list(present)}, matches={task_ids or 'NONE'}"
        )

    rule = matches[0]
    unexpected = sorted(set(present) - set(rule.source_tables))
    if unexpected:
        raise RuntimeError(
            "Legacy CN publish SQL mixes source tables outside its explicit DAG rule: "
            f"task={rule.task_id}, unexpected={unexpected}"
        )

    node = CN_FINAL_PUBLISH_DAG.node(rule.task_id)
    if node.stage_table not in rule.source_tables:
        raise RuntimeError(
            f"CN publish DAG registry mismatch for {node.task_id}: "
            f"node_stage={node.stage_table}, rule_sources={list(rule.source_tables)}"
        )
    return node


def cn_final_publish_dag_contract() -> dict:
    contract = CN_FINAL_PUBLISH_DAG.contract()
    native_count = sum(1 for node in CN_FINAL_PUBLISH_DAG.nodes if node.native_execution)
    contract["execution_mode"] = "HYBRID_NATIVE_WITH_INFLIGHT_LEGACY_COMPATIBILITY"
    contract["native_node_count"] = native_count
    contract["compatibility_node_count"] = len(CN_FINAL_PUBLISH_DAG.nodes) - native_count
    contract["legacy_rule_count"] = len(LEGACY_PUBLISH_RULES)
    contract["inflight_compatibility_policy"] = (
        "VERSIONED_PER_NODE_CUTOVER_MARKERS_PRESERVE_PREEXISTING_CHECKPOINT_EXECUTION"
    )
    return contract
