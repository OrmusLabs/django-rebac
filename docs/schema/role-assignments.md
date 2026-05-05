# Role Assignments & Tuple Injection

Because `django-rebac` bridges Django and the ReBAC engine, your Django core does not need to hardcode complex hierarchical logic. It simply passes structural strings directly to the ReBAC graph.

## The Collaboration Phase (Assigning Dynamic Roles)

When a model is initially created, the `RebacModelSyncMixin` handles the roles automatically. But what happens when you need to assign a role *after* the object exists, or without mutating a Django model at all? (e.g., Inviting a new user to an Organization, or adding a Reviewer to a Document).

You have three architectural paths to handle dynamic roles.

### Method 1: The API-Driven Approach
> ReBAC as Source of Truth

If ReBAC is your absolute source of truth, you do not need to add Many-to-Many fields to your Django models. Instead, you write the relationship directly to the `RebacSyncOutbox` using a custom DRF ViewSet action.

Django never stores the fact that "Eve is a Reviewer" in PostgreSQL. ReBAC remembers it for you.

```python
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from rebac.mixins import RebacViewMixin
from rebac.models import RebacSyncOutbox

class DocumentViewSet(RebacViewMixin, viewsets.ModelViewSet):
    rebac_config = RebacViewConfig(
        object_type="document",
        action_relations={"add_reviewer": "can_share"}
    )

    @action(detail=True, methods=["post"])
    def add_reviewer(self, request, pk=None):
        document = self.get_object() # 1. Verifies requester has 'can_share'
        new_reviewer_id = request.data.get("user_id")

        # 2. We write directly to the Outbox. Django models are bypassed!
        with transaction.atomic():
            RebacSyncOutbox.objects.create(
                action=RebacSyncOutbox.Action.WRITE.value,
                user_id=f"user:{new_reviewer_id}",
                relation="reviewer",
                object_id=f"document:{document.pk}"
            )

            # 3. (Optional) Trigger the worker immediately for instant sync
            from rebac.tasks import process_rebac_outbox_batch
            transaction.on_commit(lambda: process_rebac_outbox_batch.delay())

        return Response({"status": "Reviewer added to ReBAC graph"})
```

### Method 2: Programmatic & Background Tasks
> The Escape Hatch

Sometimes, you need to assign a role completely outside of a ViewSet (e.g., inside a Celery task, a management command, or a specialized Service class).

You can directly interact with the `RebacSyncOutbox` model to queue your own custom tuples. The Celery worker sweeps the Outbox entirely independently of how the records got there.

```python
# views.py
from rest_framework import viewsets
from rebac.permissions import IsRebacAuthorized
from rebac.models import RebacSyncOutbox  # Import the Outbox model!

from .models import Document
from .serializers import DocumentSerializer

class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [IsRebacAuthorized]
    # ... standard rebac_config variables ...

    def perform_create(self, serializer):
        # 1. Save the document normally.
        # (This triggers the Model Mixin to queue the creator/parent tuples)
        raw_user_id = self.request.rebac_user.replace("user:", "")
        document = serializer.save(creator_id=raw_user_id)

        # 2. Extract dynamic data from the POST payload
        # e.g., payload contains: {"title": "My Doc", "extra_editors": ["uuid1", "uuid2"]}
        extra_editors = self.request.data.get("extra_editors", [])

        # 3. Manually queue custom tuples into the Outbox!
        for editor_id in extra_editors:
            RebacSyncOutbox.objects.create(
                action=RebacSyncOutbox.Action.WRITE.value,
                user_id=f"user:{editor_id}",
                relation="editor",
                object_id=f"document:{document.id}"
            )

        # Once the view finishes returning the HTTP Response, the database commits,
        # and Celery sweeps up BOTH the mixin's tuples and your custom tuples at the same time!
```

### Method 3: The Django M2M Approach
> For UI-Heavy Apps

If your frontend needs to display a list of "All Reviewers" instantly without making a network call to the the ReBAC engine API, you should map a Django Many-to-Many table directly to an ReBAC role.

Create a junction model and attach the `RebacModelSyncMixin` to it:

```python
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig

class DocumentReviewer(RebacModelSyncMixin, models.Model):
    document = models.ForeignKey("Document", on_delete=models.CASCADE)
    user_id = models.CharField(max_length=255)

    rebac_config = RebacModelConfig(
        object_type="document",
        parents=[
            RebacParentConfig(
                relation="reviewer",       # The ReBAC role
                parent_type="user",        # The left side of the tuple
                local_field="user_id"      # The new reviewer's ID
            )
        ]
    )
```
Now, whenever you save a `DocumentReviewer` record in Django, the framework automatically generates: `user:{user_id} is the 'reviewer' of document:{document_id}`.


!!! tip "Which method should you use?"
    * Use **Method 1 (Model Override)** if the authorization rule depends on the fields *inside* the database row (like an `is_public` or `status` field).
    * Use **Method 2 (View `perform_create`)** if the authorization rule depends on external data passed by the user in the API request that isn't saved directly to the model.
    * Use **Method 3 (Escape Hatch)** when assigning roles without creating or mutating a model instance (e.g., inviting a user to a resource).
