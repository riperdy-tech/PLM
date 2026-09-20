# tests/unit/ar/test_storage_models.py
import sqlalchemy as sa

from services.autonomous_ar.storage.models import AR_METADATA, AR_SCHEMA, AR_TABLES

EXPECTED_TABLES = {
    "ar_content_arm_state", "ar_arm_feature_snapshot", "ar_arm_posterior",
    "ar_experiment_result", "ar_reward_observation", "ar_allocation_policy",
    "ar_allocation_cycle", "ar_allocation_decision", "ar_production_directive",
    "ar_arm_similarity", "ar_arm_exposure", "ar_baseline_config",
    "ar_posterior_update_log", "ar_channel_state", "ar_manual_override",
}


def test_schema_and_table_set_are_exact() -> None:
    assert AR_SCHEMA == "ar"
    assert AR_METADATA.schema == "ar"
    assert {table.name for table in AR_TABLES} == EXPECTED_TABLES


def test_no_native_enum_types_anywhere() -> None:
    for table in AR_TABLES:
        for column in table.columns:
            assert not isinstance(column.type, sa.Enum), (
                f"{table.name}.{column.name}: enum values are text validated in Python"
            )


def test_arm_identity_columns_are_plain_text_because_p1_mints_them() -> None:
    # a schema-bound MetaData keys its tables schema-qualified ("ar.<name>")
    state = AR_METADATA.tables["ar.ar_content_arm_state"]
    assert isinstance(state.c.arm_id.type, sa.Text) and state.c.arm_id.unique
    assert state.c.arm_id.primary_key is False  # the row id is P2's; the arm id is P1's
