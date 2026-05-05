## Database Schema vs. ReBAC Graph: When do you need a local field?

When designing your system, you must decide which relationships live in your Django database and which relationships live *exclusively* in ReBAC.

The golden rule of this framework is: **The `RebacModelSyncMixin` can only synchronize data it can see.** If you define a relation in `RebacModelConfig`, there **must** be a corresponding physical field in your Django model.

Here is how to decide where a relationship belongs:

### 1. Relations that REQUIRE a field in your Django Model
You must add a `ForeignKey`, `UUIDField`, or `CharField` to your Django `models.py` when the relationship is a core structural property of the object or its initial birth state.

These are typically **1-to-1** or **Many-to-1** relationships. Because they exist in the Django database, the `RebacModelSyncMixin` will automatically read them and sync them to ReBAC.

**Examples that need a Django field (`local_field`):**

- **Structural Parents:** A Document belongs to a Folder. You need a `folder_id` column so Django knows where to render it in the UI and how to perform cascading deletes.
- **The Initial Creator/Owner:** The user who literally clicked "Create." You need a `creator_id` column for basic audit trails.

```python
class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)

    # ⚠️ THESE REQUIRE DB COLUMNS
    folder_id = models.UUIDField()      # Structural Parent
    creator_id = models.UUIDField()     # Initial Owner

    rebac_config = RebacModelConfig(
        object_type="document",
        parents=[RebacParentConfig(relation="folder", parent_type="folder", local_field="folder_id")],
        creators=[RebacCreatorConfig(relation="owner", local_field="creator_id")]
    )
```

### 2. Relations that DO NOT require a field in your Django Model (ReBAC Handles It)

You should **not** add fields or Many-to-Many (M2M) tables to Django for highly dynamic, collaborative roles. ReBAC is built to handle these natively, keeping your database incredibly lean.

**Examples that DO NOT need a Django field:**

- **Reviewers:** A document can have 50 reviewers.
- **Editors/Viewers:** A document is shared with 100 different users.

Instead of bloating `models.py` with M2M junction tables, you write these relationships directly to the ReBAC graph. Django never stores the fact that "Eve is a Reviewer." ReBAC remembers it for you.

👉 **See the [Role Assignments Guide](./role-assignments.md) for full code examples on how to write these dynamic relationships directly to ReBAC using ViewSets and custom actions.**

---

## Mapping Django Relationships to ReBAC

When your Django model uses physical relationships (like a `models.ForeignKey`) to establish a structural parent, you must configure the `RebacModelSyncMixin` properly to read it.

### 1. The "Django Magic" Rule (`_id`)
When you define a `ForeignKey` in Django (e.g., `folder = models.ForeignKey(...)`), Django automatically creates an underlying database column and property with an `_id` suffix (e.g., `folder_id`).

**You must use this `_id` property as the `local_field` in your `RebacParentConfig`.** Do not use the related object name itself, as that would force the mixin to perform an unnecessary SQL JOIN just to read the ID!

#### Example: A Document inside a Folder
Here is a complete example of a Django model with a strict physical relationship perfectly mapped to a ReBAC structural relationship.

```python
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig

# 1. The Parent Model
class Folder(models.Model):
    name = models.CharField(max_length=255)

# 2. The Child Model
class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)

    # THE PHYSICAL DATABASE COLUMN:
    # Django will automatically create a property called `folder_id`
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name="documents")

    # THE ReBAC GRAPH MAPPING:
    rebac_config = RebacModelConfig(
        object_type="document",
        parents=[
            RebacParentConfig(
                relation="parent",           # The ReBAC relationship name
                parent_type="folder",        # The ReBAC type of the parent
                local_field="folder_id"      # <--- The exact Django DB column property!
            )
        ]
    )
```

**How the Mixin processes this:**
When you execute `document.save()`, the mixin does **not** fetch the related `Folder` object from the database. Instead, it highly efficiently reads `self.folder_id` directly from memory and generates the following tuple:

* **User:** `folder:{folder_id}`
* **Relation:** `parent`
* **Object:** `document:{id}`

### 2. Handling One-to-Many (1:N) Relationships
In a One-to-Many relationship (e.g., One `Department` has Many `Employees`), the physical database column (`ForeignKey`) always lives on the "Many" side (the Child).

Because the `RebacModelSyncMixin` relies on reading physical columns, **you must place the `RebacModelConfig` on the Child model.** The Parent model does not need any ReBAC configuration to act as a structural parent!

Let's say a Department Head automatically gets "viewer" access to all Employees within their Department. ReBAC handles this through inheritance. We just need to tell ReBAC that the Employee belongs to the Department.

```python
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig

# 1. The "One" Side (Parent)
class Department(models.Model):
    name = models.CharField(max_length=255)
    # Notice: No RebacModelSyncMixin is required here if it's just acting as a parent!

# 2. The "Many" Side (Child)
class Employee(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=255)

    # The physical database column
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="employees")

    # The ReBAC Graph Mapping goes on the CHILD
    rebac_config = RebacModelConfig(
        object_type="employee",
        parents=[
            RebacParentConfig(
                relation="department",          # The ReBAC relationship
                parent_type="department",       # The ReBAC parent type
                local_field="department_id"     # The Django DB column
            )
        ]
    )
```

---

## The `CASCADE` Deletion Trap & How to Handle It

When using `on_delete=models.CASCADE` on a `ForeignKey` (as seen in the models above), you introduce a critical distributed systems challenge.

**The Problem:** Django optimizes `CASCADE` deletions by issuing a single, bulk SQL `DELETE` query for all child objects. Bulk operations completely bypass Django's instance-level `.delete()` method. Because the `RebacModelSyncMixin` relies on intercepting `.delete()`, the cascaded child objects will be removed from your PostgreSQL database, but their corresponding relationships will be **permanently orphaned** in the ReBAC graph.

You must explicitly orchestrate this dual-write. Here are the two production-ready approaches:

### Approach 1: The Domain Service Layer
> Recommended

Instead of calling `folder.delete()` directly in a view, encapsulate the deletion logic within a Domain Service. This decouples the business logic from the ORM and forces the mixin to execute for every child.

```python
import logging
from typing import Any

from django.db import transaction

from .models import Folder, Document

logger = logging.getLogger(__name__)

class FolderService:
    """Domain service for orchestrating Folder lifecycles and ReBAC sync."""

    @staticmethod
    def delete_folder(folder: Folder) -> bool:
        """
        Safely deletes a Folder and its child Documents, explicitly triggering
        the RebacModelSyncMixin for all cascading dependencies.

        Args:
            folder: The Folder instance to be deleted.

        Returns:
            bool: True if deletion was successful.
        """
        # Fail fast on invalid inputs
        if not folder or not folder.pk:
            logger.warning("Attempted to delete an invalid or unsaved Folder.")
            return False

        try:
            with transaction.atomic():
                # 1. Fetch all child documents explicitly into memory
                documents = Document.objects.filter(folder=folder)

                # 2. Iterate and call .delete() individually.
                # This guarantees the RebacModelSyncMixin intercepts each deletion
                # and queues the ReBAC tuples into the transactional outbox.
                for doc in documents:
                    doc.delete()

                # 3. Finally, delete the parent folder
                folder.delete()

            return True
        except Exception as e:
            logger.error(f"Failed to delete Folder {folder.pk}: {e}")
            raise
```

**How it works:** It ensures strict transactional integrity. By wrapping the iteration in `transaction.atomic()`, if the ReBAC Outbox insert fails for even a single document, the physical Django deletion rolls back, maintaining absolute eventual consistency between your database and the authorization graph.

### Approach 2: Django Signals
> The Admin-Friendly Fallback

If you rely heavily on the built-in Django Admin panel or cannot refactor to a Service layer, utilize Django's `pre_delete` signal. While bulk cascades bypass the `.delete()` method, Django *does* emit `pre_delete` signals for every object destroyed during a cascade.

```python
import logging
from typing import Any

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from rebac.adapters import RebacTupleAdapter
from rebac.models import RebacSyncOutbox
from .models import Document

logger = logging.getLogger(__name__)

@receiver(pre_delete, sender=Document)
def queue_rebac_deletion_on_cascade(
    sender: type[Document],
    instance: Document,
    **kwargs: Any
) -> None:
    """
    Intercepts Document deletions, specifically catching SQL bulk cascades,
    to ensure ReBAC tuples are queued for deletion in the Outbox.

    Args:
        sender: The model class broadcasting the signal.
        instance: The specific Document being deleted.
        kwargs: Additional signal keyword arguments.
    """
    # Defensive check: Ensure instance is valid and configured for ReBAC
    if not instance.pk or not getattr(instance, 'rebac_config', None):
        return

    try:
        # 1. Generate the tuples representing the current ReBAC graph state
        tuples_to_delete = RebacTupleAdapter.generate_tuples(
            instance,
            instance.rebac_config
        )

        # 2. Utilize the mixin's internal queueing mechanism
        # This automatically triggers the Celery worker via transaction.on_commit
        for t in tuples_to_delete:
            instance._queue_outbox(RebacSyncOutbox.Action.DELETE, t)

    except Exception as e:
        logger.error(f"Signal failure during ReBAC tuple generation for {instance.pk}: {e}")
```

**How it works:** It provides a "catch-all" safety net that works even when a developer (or the Django Admin interface) triggers a cascade directly via the ORM (`Folder.objects.filter(...).delete()`).
