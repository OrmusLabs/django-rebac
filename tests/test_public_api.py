"""Guards the documented public API surface.

Every import path asserted here appears verbatim in `README.md` or under `docs/`. They were
all broken at one point -- `rebac.mixins`, `rebac.structs`, and `rebac.adapters` did not
exist, and the package root was empty -- so every documented snippet raised ImportError.
Keep this in sync with the docs: if a path is documented, assert it here.
"""

import importlib
import importlib.util

import pytest


class TestFlatReExports:
    """The flat modules must resolve to the very same objects as the canonical ones."""

    def test_rebac_mixins(self):
        from rebac.mixins import RebacModelSyncMixin, RebacViewMixin
        from rebac.models.mixins import RebacModelSyncMixin as CanonicalModelMixin
        from rebac.views.mixins import RebacViewMixin as CanonicalViewMixin

        assert RebacModelSyncMixin is CanonicalModelMixin
        assert RebacViewMixin is CanonicalViewMixin

    def test_rebac_structs(self):
        from rebac import core
        from rebac.structs import (
            RebacCreatorConfig,
            RebacModelConfig,
            RebacParentConfig,
            RebacViewConfig,
        )

        assert RebacModelConfig is core.RebacModelConfig
        assert RebacParentConfig is core.RebacParentConfig
        assert RebacCreatorConfig is core.RebacCreatorConfig
        assert RebacViewConfig is core.RebacViewConfig

    def test_rebac_adapters(self):
        from rebac.adapters import RebacTupleAdapter
        from rebac.core.adapters import RebacTupleAdapter as Canonical

        assert RebacTupleAdapter is Canonical


class TestPackageRoot:
    """`from rebac import X` is documented in docs/examples/frontend-integration.md."""

    @pytest.mark.parametrize(
        ("name", "canonical_module"),
        [
            ("IsRebacAuthorized", "rebac.permissions"),
            ("RebacCreatorConfig", "rebac.core.structs"),
            ("RebacModelConfig", "rebac.core.structs"),
            ("RebacModelSyncMixin", "rebac.models.mixins"),
            ("RebacParentConfig", "rebac.core.structs"),
            ("RebacPermissionSerializerMixin", "rebac.serializers.mixins"),
            ("RebacSyncOutbox", "rebac.models.outbox"),
            ("RebacTupleAdapter", "rebac.core.adapters"),
            ("RebacViewConfig", "rebac.core.structs"),
            ("RebacViewMixin", "rebac.views.mixins"),
        ],
    )
    def test_lazy_attribute_resolves_to_canonical_object(self, name, canonical_module):
        import rebac

        assert getattr(rebac, name) is getattr(importlib.import_module(canonical_module), name)

    def test_all_is_complete(self):
        import rebac

        for name in rebac.__all__:
            assert getattr(rebac, name) is not None

    def test_unknown_attribute_raises_attribute_error(self):
        import rebac

        with pytest.raises(AttributeError, match="has no attribute 'NopeNotAThing'"):
            _ = rebac.NopeNotAThing

    def test_root_does_not_eagerly_import_models(self):
        """The package root must stay model-free at import time.

        `rebac` is in INSTALLED_APPS, so Django imports it while the app registry is still
        being populated. An eager model import here raises AppRegistryNotReady on boot.
        """
        source = importlib.util.find_spec("rebac").origin
        with open(source, encoding="utf-8") as fh:
            module_source = fh.read()

        # The only model imports allowed are inside the `if TYPE_CHECKING:` block.
        runtime_source = module_source.split("if TYPE_CHECKING:")[0]
        assert "from .models" not in runtime_source
        assert "from .views" not in runtime_source
        assert "from .serializers" not in runtime_source
        assert "from .permissions" not in runtime_source


class TestDocumentedCanonicalPaths:
    """Paths the docs use directly, which must keep working."""

    @pytest.mark.parametrize(
        ("module", "name"),
        [
            ("rebac.conf", "get_setting"),
            ("rebac.utils", "get_rebac_client"),
            ("rebac.models", "RebacSyncOutbox"),
            ("rebac.models", "RebacModelSyncMixin"),
            ("rebac.permissions", "IsRebacAuthorized"),
            ("rebac.serializers", "RebacPermissionSerializerMixin"),
            ("rebac.tasks", "process_rebac_outbox_batch"),
            ("rebac.middleware", "GatewayIdentityMiddleware"),
            ("rebac.views", "RebacViewMixin"),
        ],
    )
    def test_importable(self, module, name):
        assert hasattr(importlib.import_module(module), name)
