# Syncing Models to ReBAC Backend

To synchronize a Django model with ReBAC, simply inherit from `RebacModelSyncMixin` and define your `rebac_config` using the `RebacModelConfig` dataclass. The package handles everything else automatically.
## The "Ownership" Rule: When to use the Mixin

Before adding the `RebacModelSyncMixin` to your models, you must ask one architectural question: **Does this specific mini-app "own" this data?**

In a distributed microservice environment, you will interact with two types of data: data you *own* (Source of Truth) and data you *borrow* (External Context).

### When to USE the Mixin
> Source of Truth

You **MUST** use the `RebacModelSyncMixin` when your mini-app is the authoritative creator of a resource.
When a user creates this object in your app, ReBAC needs to know about it instantly so it can assign the creator their roles.

* **Example:** The Finance App owns `Invoice` and `Expense` records.
* **Action:** You attach the mixin to the `Invoice` model. When `invoice.save()` is called, the mixin writes `user:alice -> owner -> invoice:123` to the ReBAC graph.

### When NOT to use the Mixin
> Borrowed Context

You **MUST NOT** use the `RebacModelSyncMixin` for resources that your mini-app merely references but does not natively create or manage.

* **Example:** The Finance App groups invoices by `Organization`. The central "Core Identity" service owns the `Organization` data, not the Finance App.
* **Action:** If you create a read-only `Organization` table in your Finance database (or just store `organization_id` strings), **do not attach the mixin to it**. If you did, saving an organization in the Finance app might accidentally overwrite or conflict with the Core service's ReBAC tuples!
* **How to authorize it:** To check permissions against borrowed context, completely bypass your local models and use [Stateless Views](stateless-views.md) (e.g., `lookup_header="HTTP_X_CONTEXT_ORG_ID"`).

!!! tip "The Architect's Summary"
    * **Writing to the Graph:** Use `RebacModelSyncMixin` on models your app explicitly creates.
    * **Reading from the Graph:** Use `RebacViewConfig(lookup_header=...)` on endpoints that check permissions against external parents.

### Example: Defining Cascading Inheritance & Roles

*Assume the following code lives in the Central Core service (which **owns** Organizations) and the Document service (which **owns** Folders and Documents).*

```python
# models.py
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig, RebacCreatorConfig
from typing import ClassVar

class Organization(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=255)
    creator_id = models.UUIDField()

    rebac_config: ClassVar[RebacModelConfig] = RebacModelConfig(
        object_type="organization",
        creators=[
            RebacCreatorConfig(
                relation="admin",
                local_field="creator_id"
            )
        ]
    )

class Folder(RebacModelSyncMixin, models.Model):
    name = models.CharField(max_length=255)
    organization_id = models.UUIDField()
    creator_id = models.UUIDField()

    rebac_config: ClassVar[RebacModelConfig] = RebacModelConfig(
        object_type="folder",
        parents=[
            RebacParentConfig(
                relation="organization",
                parent_type="organization",
                local_field="organization_id"
            )
        ],
        creators=[
            RebacCreatorConfig(
                relation="owner",
                local_field="creator_id"
            )
        ]
    )

class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    folder_id = models.UUIDField()
    creator_id = models.UUIDField()

    rebac_config: ClassVar[RebacModelConfig] = RebacModelConfig(
        object_type="document",
        parents=[
            RebacParentConfig(
                relation="folder",
                parent_type="folder",
                local_field="folder_id"
            )
        ],
        creators=[
            RebacCreatorConfig(
                relation="editor",
                local_field="creator_id"
            )
        ]
    )
```

Whenever you call `Document.objects.create()`, `document.save()`, or `document.delete()`, the mixin will automatically calculate the graph diffs, queue the tuples in the local Outbox table, and trigger the Celery worker to push them to ReBAC asynchronously.

---



### 🏗️ The Multi-Parent & Multi-Creator Architecture

Imagine a scenario where a `Document` belongs to both a `Folder` and a `Project` (Multiple Parents). Furthermore, when it is created, it assigns both an `author` and an initial `reviewer` (Multiple Creators).

Here is the perfect example to add to your documentation or docstrings:

```python
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig, RebacCreatorConfig

class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)

    # Structural Links (Multiple Parents)
    folder_id = models.UUIDField()
    project_id = models.UUIDField(null=True, blank=True)

    # Role Assignments (Multiple Creators)
    author_id = models.UUIDField()
    reviewer_id = models.UUIDField()

    rebac_config = RebacModelConfig(
        object_type="document",

        # 🌳 MULTIPLE PARENTS
        # The document inherits permissions from BOTH the folder and the project.
        parents=[
            RebacParentConfig(
                relation="folder",
                parent_type="folder",
                local_field="folder_id"
            ),
            RebacParentConfig(
                relation="project",
                parent_type="project",
                local_field="project_id"
            )
        ],

        # 👥 MULTIPLE CREATORS
        # Both the author and the reviewer are explicitly assigned roles upon creation.
        creators=[
            RebacCreatorConfig(
                relation="author",
                local_field="author_id"
            ),
            RebacCreatorConfig(
                relation="reviewer",
                local_field="reviewer_id"
            )
        ]
    )
```

!!! tip "Handling Null Values"
    If a `Document` is saved without a `project_id` (evaluating to `None`), the framework safely ignores it and only generates tuples for fields that actually contain data.

---

## 1. The Tuple Mapping (Graph)
This diagram shows how the `RebacModelConfig` dataclass acts as a translation layer, reading soft-reference `UUIDs` from your Django Model and converting them into strict Zanzibar Tuples.

```mermaid
%%{
  init: {
    'fontFamily': 'Roboto, sans-serif'
  }
}%%
graph LR
    %% Light/Dark Mode Compatible Styling
    classDef django fill:none,stroke:#059669,stroke-width:3px
    classDef mapping fill:none,stroke:#888888,stroke-width:2px,stroke-dasharray: 5 5
    classDef ReBAC fill:none,stroke:#3b82f6,stroke-width:3px

    subgraph Django Model Instance
        D[Document<br/>id: doc_123]:::django
        F[folder_id: fld_456]:::django
        C[creator_id: usr_789]:::django
        D --- F
        D --- C
    end

    subgraph RebacModelConfig
        P_Map[Parents Definition<br/>relation: 'folder'<br/>parent_type: 'folder']:::mapping
        C_Map[Creators Definition<br/>relation: 'editor']:::mapping
        F -.->|local_field| P_Map
        C -.->|local_field| C_Map
    end

    subgraph Generated ReBAC Tuples
        T1["User: <b>folder:fld_456</b><br/>Relation: <b>folder</b><br/>Object: <b>document:doc_123</b>"]:::ReBAC
        T2["User: <b>user:usr_789</b><br/>Relation: <b>editor</b><br/>Object: <b>document:doc_123</b>"]:::ReBAC
    end

    P_Map ===>|Translates to| T1
    C_Map ===>|Translates to| T2
```

---

## 2. The Transactional Outbox Lifecycle (Sequence)
This diagram illustrates the underlying superpower of the `RebacModelSyncMixin`. It shows why calling `.save()` is 100% reliable, protecting your system against network failures to the ReBAC server.

```mermaid
sequenceDiagram
    autonumber
    actor Code as Service Layer
    participant Model as Document Model
    participant Mixin as RebacModelSyncMixin
    participant DB as PostgreSQL (DB)
    participant Celery as Celery Worker
    participant ReBAC as ReBAC Server

    Code->>Model: Document.objects.create(title="...")
    Model->>Mixin: intercept save()

    rect rgb(240, 248, 255)
        Note over Mixin, DB: Atomic Database Transaction
        Mixin->>Mixin: Calculate Tuples from RebacModelConfig
        Mixin->>DB: BEGIN TRANSACTION
        DB->>DB: Save Document Data
        Mixin->>DB: Insert into RebacSyncOutbox (Status: Pending)
        DB->>DB: COMMIT TRANSACTION
    end

    Mixin->>Celery: process_rebac_outbox_batch.delay() (Fires strictly on_commit)

    rect rgb(245, 245, 245)
        Note over Celery, ReBAC: Asynchronous Background Sync
        Celery->>DB: SELECT FOR UPDATE (Lock pending rows)
        Celery->>ReBAC: HTTP POST Batch Write Request
        ReBAC-->>Celery: 200 OK (Graph Updated)
        Celery->>DB: Bulk Update Outbox (Status: Synced)
    end
```

---

## 3. Overriding the Rules & Custom Logic

If you need to inject custom business logic or manipulate tuples in the middle of the process, you have three clean "escape hatches" depending on where the data originates.

### Method 1: The Model Level
> Overriding `save`

If the custom role assignment is tied directly to the data state of the model (for example, making a document "Public" based on a boolean field), you should intercept the `save()` method. Because we use the Outbox pattern, you can queue tuples manually using `self._queue_outbox`.

```python
# models.py
from django.db import models
from rebac.mixins import RebacModelSyncMixin
from rebac.models import RebacSyncOutbox
from rebac.structs import RebacModelConfig

class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)
    folder_id = models.UUIDField()
    creator_id = models.UUIDField()

    # Let's say we have a custom boolean field
    is_public = models.BooleanField(default=False)

    rebac_config = RebacModelConfig(...) # Define standard config here

    def save(self, *args, **kwargs):
        # 1. Let the mixin handle the standard config-based tuples
        super().save(*args, **kwargs)

        # 2. Inject your custom, dynamic logic!
        if self.is_public:
            self._queue_outbox(
                action=RebacSyncOutbox.Action.WRITE.value,
                t={
                    "user": "user:*",                 # ReBAC wildcard for "everyone"
                    "relation": "reader",             # The role to assign
                    "object": f"document:{self.pk}"   # This specific document
                }
            )
```
> **Note:** Because the mixin automatically calculates diffs based on the original state versus the new state, custom manual tuples like the one above will need to be manually deleted if `is_public` reverts to `False`.

!!! tip "Need to assign roles outside of models?"
    If you need to assign ReBAC roles via HTTP Requests (View Level) or directly via background tasks, completely bypass the model layer. 👉 **See the [Role Assignments Guide](../schema/role-assignments.md) for full programmatic implementations.**
