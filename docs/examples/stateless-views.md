# Stateless Views & Pluggable Dashboards

In a distributed microservice architecture, your mini-apps will often need to authorize requests against external resources that they do not own.

For example, a Finance Mini-App might have a `FinanceDashboardView`. This dashboard aggregates data from local tables (like `Invoice` and `Expense`), but the authorization gate sits at the **Organization** level (e.g., *"Is this user a finance admin of the active organization?"*).

Because the Finance Mini-App does not have an `Organization` table in its local PostgreSQL database, it cannot rely on standard stateful Django models for authorization.

We solve this using **Stateless Resolution**.

---

## The Stateless Pattern
> Using `lookup_header` in RebacViewConfig

By utilizing the `lookup_header` (or `Workspace_kwarg`) in your `RebacViewConfig`, the `IsRebacAuthorized` permission class will bypass the local database entirely. It extracts the target Object ID directly from the incoming HTTP request and queries ReBAC.

Here is a complete example of a highly decoupled, pluggable Dashboard View.

### 1. The Dynamic Settings
To make your mini-app truly pluggable, avoid hardcoding model imports. Define the target models in your Django `settings.py` so other teams can override them if they install your app.

```python
# settings.py
FINANCE_DASHBOARD_CONFIG = {
    "INVOICE_MODEL": "finance_app.Invoice",
    "EXPENSE_MODEL": "finance_app.Expense",
}
```

### 2. The Dashboard View
Notice how this view does not import any local models at the top of the file, and relies entirely on the `HTTP_X_CONTEXT_ORG_ID` header for its ReBAC authorization check.

```python
# views.py
from typing import ClassVar

from django.apps import apps
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response

from rebac.permissions import IsRebacAuthorized
from rebac.structs import RebacViewConfig

class FinanceDashboardView(APIView):
    """
    A 100% Stateless and Pluggable Dashboard.
    Authorizes via headers and loads models dynamically via settings.
    """
    permission_classes: ClassVar[list] = [IsRebacAuthorized]

    # 🛡️ THE STATELESS GUARDRAIL
    # Bypasses the local DB and checks the graph directly!
    #
    # 1. Identity: "user:alice_123" (Auto-extracted by TraefikIdentityMiddleware)
    # 2. Target: "acme_corp" (Extracted from HTTP_X_CONTEXT_ORG_ID via lookup_header)
    #
    # Q: "Does `user:alice_123` have the `can_view_finance_dashboard`
    # permission on `organization:acme_corp`?"
    rebac_config = RebacViewConfig(
        object_type="organization",
        read_relation="can_view_finance_dashboard",
        lookup_header="HTTP_X_CONTEXT_ORG_ID"
    )

    def get(self, request):
        # 1. Extract the authorized Org ID from the Traefik Gateway
        org_id = request.META.get("HTTP_X_CONTEXT_ORG_ID")

        # 2. Dynamically load the configured models into memory
        dashboard_config = getattr(settings, "FINANCE_DASHBOARD_CONFIG", {})

        InvoiceModel = apps.get_model(
            dashboard_config.get("INVOICE_MODEL", "finance_app.Invoice")
        )
        ExpenseModel = apps.get_model(
            dashboard_config.get("EXPENSE_MODEL", "finance_app.Expense")
        )

        # 3. Query the dynamically loaded models!
        total_invoices = InvoiceModel.objects.filter(organization_id=org_id).count()
        total_expenses = ExpenseModel.objects.filter(organization_id=org_id).count()

        return Response({
            "dashboard_target": org_id,
            "metrics": {
                "total_invoices": total_invoices,
                "total_expenses": total_expenses
            }
        })
```


### 3. The Underlying Models (Context)

To complete the picture, here is what the models in the `finance_app` look like.

Notice that we **DO** use the `RebacModelSyncMixin` on these models! This perfectly demonstrates the **Ownership Rule**: The Finance App does not own the `Organization` (so the dashboard view checks it statelessly), but the Finance App *does* own the `Invoice` and `Expense` records, so it must sync their creation to ReBAC.

```python
# finance_app/models.py
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig, RebacCreatorConfig

class Invoice(RebacModelSyncMixin, models.Model):
    # The physical link to the external Organization
    organization_id = models.CharField(max_length=255, db_index=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    creator_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    # 🌳 Syncs the Invoice to the ReBAC Graph upon creation
    rebac_config = RebacModelConfig(
        object_type="invoice",
        parents=[
            RebacParentConfig(
                relation="organization",
                parent_type="organization",
                local_field="organization_id"
            )
        ],
        creators=[
            RebacCreatorConfig(
                relation="creator",
                local_field="creator_id"
            )
        ]
    )

class Expense(RebacModelSyncMixin, models.Model):
    organization_id = models.CharField(max_length=255, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    creator_id = models.CharField(max_length=255)

    rebac_config = RebacModelConfig(
        object_type="expense",
        parents=[
            RebacParentConfig(
                relation="organization",
                parent_type="organization",
                local_field="organization_id"
            )
        ],
        creators=[
            RebacCreatorConfig(
                relation="spender",
                local_field="creator_id"
            )
        ]
    )
```

### 4. The ReBAC Schema

To make the code above function perfectly, your central ReBAC server needs a schema that understands these relationships.

Notice how the schema perfectly bridges the two concepts we just discussed:

1. **The View:** Checks the `can_view_finance_dashboard` permission directly on the `organization`.
2. **The Models:** Push tuples into the `organization` and `creator`/`spender` relations on the child objects.

```yaml
model
  schema 1.1

type user

# ==========================================
# THE EXTERNAL CONTEXT
# ==========================================
type organization
  relations
    # 1. Base Roles
    define member: [user]

    define admin: [user] and member
    define manager: [user] and member
    define expert: [user]

    # 2. Conditional Role or Shadow Role (The Intersection)
    # This role cannot be assigned directly; it is strictly inherited
    # from the admin role to ensure they are always a member.
    define finance_admin: admin or manager

    # 3. Permissions (The Stateless Dashboard checks this!)
    define can_view_finance_dashboard: finance_admin
    define can_invite_expert: finance_admin

# ==========================================
# THE OWNED DATA
# ==========================================
type invoice
  relations
    define organization: [organization]
    define creator: [user]

    # cascading permission
    define can_read_invoice: creator or finance_admin from organization


type expense
  relations
    # 1. Structural Link (Generated by RebacParentConfig)
    define parent_org: [organization]

    # 2. Ownership (Generated by RebacCreatorConfig)
    define spender: [user]

    # 3. Permissions (Optional: cascading from the Org)
    define can_read_expense: spender or finance_admin from parent_org
```

### Architectural Benefits
1. **Zero Circular Imports:** Models are loaded at runtime inside the `get()` method.
2. **Lean Databases:** You do not need to duplicate the `Organization` table across every single mini-app just to perform authorization checks.
3. **Ultimate Reusability:** Your mini-app can be installed as a third-party package by other teams, and they can point the dashboard to their own custom database tables!
