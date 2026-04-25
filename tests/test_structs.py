# tests/test_structs.py
import pytest

from rebac.structs import (
    RebacCreatorConfig,
    RebacModelConfig,
    RebacParentConfig,
    RebacViewConfig,
)


class TestFGAStructValidators:
    def test_model_config_empty_object_type(self):
        """Verifies FGAModelConfig rejects an empty object_type."""
        with pytest.raises(ValueError, match="must define an 'object_type'"):
            RebacModelConfig(object_type="")

    def test_model_config_overlapping_relations(self):
        """Verifies FGAModelConfig rejects ambiguous relation setups."""
        with pytest.raises(ValueError, match="cannot define the same relation"):
            RebacModelConfig(
                object_type="document",
                parents=[
                    RebacParentConfig(relation="owner", parent_type="org", local_field="org_id")
                ],
                creators=[RebacCreatorConfig(relation="owner", local_field="user_id")],
            )

    def test_view_config_partial_parent_setup(self):
        """Verifies RebacViewConfig forces all or nothing for parent creation logic."""
        with pytest.raises(ValueError, match="must all be provided"):
            # Missing create_scope_field and create_relation
            RebacViewConfig(object_type="document", create_scope_type="folder")
