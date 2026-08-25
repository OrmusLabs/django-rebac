# Django ReBAC

A declarative, strictly-typed ReBAC framework for Django.

This package provides a complete "Holy Trinity" for enterprise authorization:
1. **Data Layer:** Automatically synchronizes Django models to OpenFGA using the Transactional Outbox pattern.
2. **Routing Layer:** Secures Django REST Framework (DRF) Views with zero-business-logic mixins and permission classes.
3. **Presentation Layer:** Injects high-performance, batch-evaluated permission flags into DRF Serializers for seamless React/Vue frontend integration.

## 📦 Installation

Install the package via pip or uv:

```bash
pip install django-rebac
```

Add it to your `INSTALLED_APPS` and include the Middleware in `settings.py`:

```python
INSTALLED_APPS = [
    # ... your other apps ...
    'rebac',
]

MIDDLEWARE = [
    # ... standard middleware ...
    'rebac.middleware.GatewayIdentityMiddleware',
]
```

Run migrations to create the Outbox table in your database:

```bash
python manage.py migrate rebac
```

## ⚙️ Configuration

Configure the package by adding the `REBAC_CONFIG` dictionary to your `settings.py`.

```python
# settings.py

REBAC_CONFIG = {
    # REQUIRED: The Store ID provisioned by the Central Auth Service
    "BACKEND_OPTIONS":{
        "STORE_ID": "01H...XYZ",
        "API_URL": "http://localhost:8080",
    },
    # Core Settings
    "BATCH_SIZE": 50,
    "MAX_RETRIES": 5,

    # Identity Management (Traefik / API Gateway integration)
    "REQUEST_HEADER_MAPPINGS": {
        "X-User-Id": "rebac_user",
    },
    "REBAC_USER_ATTR": "rebac_user",
    "REBAC_USER_PREFIX": "user:",
}
```

## 💡 Usage

### 1. Synchronizing Models (`RebacModelSyncMixin`)

Inherit from `RebacModelSyncMixin` and define your `rebac_config` using the `RebacModelConfig` dataclass. The package handles tuple generation, diffing, and outbox queuing automatically.

```python
from django.db import models
from typing import ClassVar
from rebac.mixins import RebacModelSyncMixin
from rebac.structs import RebacModelConfig, RebacParentConfig, RebacCreatorConfig

class Document(RebacModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)
    folder_id = models.CharField(max_length=255)
    creator_id = models.CharField(max_length=255)

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

### 2. Securing API Views (`RebacViewMixin`)

Secure your DRF endpoints instantly using simple, declarative dictionary configurations. No complex permission classes required. `RebacViewMixin` handles queryset filtering (lists), parent checks (creation), and object checks (updates/deletes).

```python
from rest_framework import viewsets
from rebac.mixins import RebacViewMixin
from rebac.structs import RebacViewConfig
from .models import Document
from .serializers import DocumentSerializer

class DocumentViewSet(RebacViewMixin, viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    rebac_config = RebacViewConfig(
        object_type="document",
        read_relation="can_read_document",
        update_relation="can_update",
        delete_relation="can_delete",

        # Parent-Level Authorization for Creation (POST)
        # Verifies the user has permission on the parent scope before allowing creation
        create_scope_type="folder",
        create_scope_field="folder_id",
        create_relation="can_add_items"
    )
```

### 3. Frontend Integration (`RebacPermissionSerializerMixin`)

Inject ReBAC evaluations directly into your API responses so your frontend knows exactly which action buttons to render. The mixin utilizes advanced custom list serializers to prevent N+1 queries, batching all checks into a single OpenRebac network request.

```python
from rest_framework import serializers
from rebac.serializers import RebacPermissionSerializerMixin
from .models import Document

class DocumentSerializer(RebacPermissionSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Document
        # The mixin automatically injects "_permissions" into this tuple!
        fields = ("id", "title", "folder_id")

        # Declarative rules processed by the mixin
        rebac_object_type = "document"
        rebac_permissions = ("can_update", "can_delete")
```

**Resulting JSON Payload:**
```json
{
  "id": 101,
  "title": "Q3 Financials",
  "folder_id": "folder_55",
  "_permissions": {
    "can_update": true,
    "can_delete": false
  }
}
```

## 🕸️ Celery Configuration

Because this package uses the Transactional Outbox pattern for model syncing, you must have Celery configured in your project to process the queued network requests.

Configure a Celery Beat sweeper to run periodically as a fail-safe:

```python
# celery.py
from celery.schedules import crontab

app.conf.beat_schedule = {
    'rebac-outbox-sweeper': {
        'task': 'rebac.tasks.process_rebac_outbox_batch',
        'schedule': crontab(minute='*/5'), # Sweep the Outbox every 5 minutes
    },
}
```
