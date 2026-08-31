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

## Deletions, `CASCADE`, and Bulk Operations

**Good news first: deletions are handled for you automatically.** When a model class
inherits `RebacModelSyncMixin`, the framework connects a `pre_delete` receiver
(`_rebac_auto_cascade_handler`) for that class automatically (via `__init_subclass__`).
Every deletion path that goes through the Django ORM therefore already queues the
correct `DELETE` tuples in the outbox, including:

- `instance.delete()`
- `QuerySet.delete()` (e.g., `Document.objects.filter(is_archived=True).delete()`)
- True FK `on_delete=models.CASCADE` edges — delete a `Folder` and the tuples of every
  cascaded `Document` are queued for removal.

> !!! warning "Do NOT register your own `pre_delete` receiver for ReBAC cleanup"
> The framework already installs one for every `RebacModelSyncMixin` subclass.
> A second receiver queues **duplicate** `DELETE` rows for the same tuple in the
> outbox, polluting the queue and (against a non-idempotent backend) poisoning the
> entire sync batch.

### Performance note: large deletions are slow, not broken

Because the receiver fires once per row, Django's fast-delete path is disabled for
these models: a 100k-row `QuerySet.delete()` loads 100k instances and inserts one
outbox row per tuple in a single transaction. That is correct behavior, but it will
be slow and memory-hungry. For very large cleanups, delete in bounded chunks (for
example, loop over a pk range with `iterator(chunk_size=1000)`) instead of one
giant `delete()` call.

### What is NOT covered

These paths change rows without calling `save()` or firing signals — ReBAC tuples
will silently stop matching the database:

| Bypass | Why it happens | Remediation |
|--------|---------------|-------------|
| `QuerySet.update(...)` | Raw SQL, no signals, no `save()` | Save instances individually, or enqueue the new tuples via `RebacTupleIngestionService.queue_tuples(...)` |
| `QuerySet.bulk_update(...)` | Same as above | Same as above |
| `Manager.bulk_create(...)` | Skips `save()` entirely | Save individually, or enqueue via `queue_tuples(...)` |
| Raw SQL, DB-level triggers, or cascades created outside Django | Never enter the Django ORM | Keep such edges out of ReBAC-managed models, or sync manually |

`RebacTupleIngestionService.queue_tuples(tuples)` bulk-enqueues a list of
`{"user", "relation", "object"}` dictionaries in a single `bulk_create` — the
intended tool for backfills and mass operations.
