# finance_app/views.py
from typing import ClassVar

from rest_framework.response import Response
from rest_framework.views import APIView

from rebac.core.structs import RebacViewConfig
from rebac.permissions import IsRebacAuthorized

# (Assuming these models exist in your Finance mini-app)
from .models import Expense, Invoice


class FinanceDashboardView(APIView):
    """
    A stateless dashboard view. It aggregates data from multiple tables,
    but authorizes the user based on the Traefik Organization header.
    """

    permission_classes: ClassVar[list] = [IsRebacAuthorized]

    # 🤠 THE STATELESS GUARDRAIL
    rebac_config = RebacViewConfig(
        object_type="organization",
        read_relation="can_view_finance_dashboard",
        lookup_header="HTTP_X_CONTEXT_ORG_ID",
    )

    def get(self, request):
        # 1. We know the user is authorized for this specific Organization!
        # We extract the Org ID safely to filter our local tables.
        org_id = request.headers.get("x-context-org-id")

        # 2. Aggregate data from different tables
        total_invoices = Invoice.objects.filter(organization_id=org_id).count()
        total_expenses = Expense.objects.filter(organization_id=org_id).count()

        # 3. Return the Dashboard Payload
        return Response(
            {
                "dashboard_target": org_id,
                "metrics": {
                    "total_invoices": total_invoices,
                    "total_expenses": total_expenses,
                },
            }
        )
