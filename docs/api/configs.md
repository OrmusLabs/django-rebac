# 🛡️ Configurations Reference

The `django-rebac` package utilizes strict Python `dataclasses` to define authorization rules. This ensures type safety, auto-completion in modern IDEs, and prevents misconfiguration before your app even boots.

There are two primary configuration classes you will use: `RebacModelConfig` (for database models) and `RebacViewConfig` (for API views).

---

## Configuration Defaults

::: rebac.conf.DEFAULTS
    options:
      show_root_heading: true
      show_source: true
      heading_level: 4

!!! warning "Setting Variable Name"
    Avoiding to use `DEFAULT = ...`.
    The correct variable name in `settings.py` is `REBAC_CONFIG = { ... }` !!!

## 1. View Configuration

The `RebacViewConfig` dataclass centralizes all ReBAC authorization rules for your Django Views and ViewSets. By attaching this configuration to your view, the underlying permission classes (`IsRebacAuthorized`) and mixins (`RebacViewMixin`) automatically enforce access control.

!!! tip "The Golden Rule: Check Permissions, Not Roles"
    When configuring a view, you must only check **Permissions** (e.g., `can_read_document`, `can_update`). You should never check roles directly.

::: rebac.core.structs.RebacViewConfig
    options:
      show_root_heading: false
      heading_level: 4
      filters:
        - "!^__post_init__$"

---

## 2. Model Configuration

The `RebacModelConfig` dataclass acts as a translation layer. It reads soft-reference identifiers (like UUIDs) from your Django Model instances and converts them into strict Zanzibar Tuples using the Transactional Outbox pattern.

!!! tip "The Golden Rule: Assign Roles, Not Permissions"
    When configuring a model's `creators` or `parents`, you must only assign base **Roles** (e.g., `owner`, `editor`). Models should never directly grant atomic permissions.

::: rebac.core.structs.RebacModelConfig
    options:
      show_root_heading: false
      heading_level: 4
      filters:
        - "!^__post_init__$"

::: rebac.core.structs.RebacParentConfig
    options:
      show_root_heading: false
      heading_level: 4
      filters:
        - "!^__post_init__$"

::: rebac.core.structs.RebacCreatorConfig
    options:
      show_root_heading: false
      heading_level: 4
      filters:
        - "!^__post_init__$"
