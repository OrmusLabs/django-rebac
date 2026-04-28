# Advanced Parent Resolution: The Model Property Fallback

When securing `POST` (creation) endpoints, the ReBAC validation phase happens *before* the Django model is instantiated and saved. By default, `RebacViewConfig` expects the frontend to provide the parent's ID directly in the JSON payload (via `create_scope_field`).

However, for **Singletons**, **Tenant Roots**, or **Global Platform** objects, forcing the frontend to send a hardcoded ID (e.g., `{"platform_id": "global"}`) leaks backend architecture to the client.

To solve this cleanly, `django-rebac` supports the **Model Property Fallback**. If the parent field is missing from the payload, the package will automatically instantiate a dummy instance of your model, read the field as a property, and use it for authorization.

## The ReBAC Architecture

In this scenario, we have a global `platform` node. Platform Admins have the "God Mode" ability to create new companies.

Here is the ReBAC schema (for OpenFGA is DSL) we are targeting:

```yaml
model
  schema 1.1

type user

# ==========================================
# LEVEL 0: THE SINGLETON ROOT
# ==========================================
type platform
  relations
    # 1. Role
    define admin: [user]

    # 2. Permission(s)
    define can_create_company: admin

# ==========================================
# LEVEL 1: THE COMPANY
# ==========================================
type company
  relations
    # 1. Structural Link to the Root
    define platform: [platform]

    # 2. Roles (Platform Admins automatically inherit Company Admin rights!)
    define admin: [user] or admin from platform
    define viewer: [user] or admin

    # 3. Permissions
    define can_read_company: viewer
    define can_update_company: admin
    define can_delete_company: admin
```

## How to implement the Fallback

### 1. Define the Property on the Model
Instead of a database field, define a `@property` on your model that returns the static ID (e.g., `"global"`). The `RebacModelSyncMixin` will use this property to automatically link every new Company to `platform:global`.

```python
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig

class Company(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=255)
    creator_id = models.CharField(max_length=100)

    # THE TRICK: Provide a static property for the ReBAC Mixin to read
    @property
    def platform_id(self) -> str:
        return "global"

    rebac_config = RebacModelConfig(
        object_type="company",
        parents=[
            RebacParentConfig(
                relation="platform",
                parent_type="platform",
                local_field="platform_id", # Maps to the property above!
            )
        ],
    )
```

!!! info "Automated ReBAC Linking"
    This model configuration serves a dual purpose! When a new `Company` is successfully saved to the database, the `RebacModelSyncMixin` automatically reads the `platform_id` property and queues an Outbox task to write the structural link to ReBAC (`object: company:{id}, relation: platform, user: platform:global`). You do not need to write any custom signals or service code to build the hierarchy!

### 2. Configure the View
In your ViewSet, point `create_scope_field` to the exact name of your model property.

```python
from rest_framework import viewsets
from rebac.mixins import RebacViewMixin
from rebac.structs import RebacViewConfig

class CompanyViewSet(RebacViewMixin, viewsets.ModelViewSet):
    queryset = Company.objects.all()
    serializer_class = CompanySerializer

    rebac_config = RebacViewConfig(
        object_type="company",
        read_relation="can_read_company",
        update_relation="can_update_company",
        delete_relation="can_delete_company",

        # 🛡️ Lock down creation to Platform Admins
        create_scope_type="platform",
        create_scope_field="platform_id", # The library will auto-resolve this from the Model!
        create_relation="can_create_company",
    )
```

!!! tip "Zero Frontend Hacks Required"
    Your frontend developers can now send a clean payload: `{"name": "Acme Corp"}`. They do not need to know about `platform_id`. The library intercepts the `POST` request, spins up an empty `Company` model in memory, reads `platform_id="global"`, and securely validates it against the ReBAC network.
