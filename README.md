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
    'rebac.middleware.TraefikIdentityMiddleware',
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
    "OPENFGA_STORE_ID": "01H...XYZ",

    # Core Settings
    "OPENFGA_API_URL": "http://localhost:8080",
    "BATCH_SIZE": 50,
    "MAX_RETRIES": 5,

    # Identity Management (Traefik / API Gateway integration)
    "REQUEST_HEADER_MAPPINGS": {
        "X-User-Id": "fga_user",
    },
    "FGA_USER_ATTR": "fga_user",
    "FGA_USER_PREFIX": "user:",
}
```

## 💡 Usage

### 1. Synchronizing Models (`FGAModelSyncMixin`)

Inherit from `FGAModelSyncMixin` and define your `fga_config` using the `FGAModelConfig` dataclass. The package handles tuple generation, diffing, and outbox queuing automatically.

```python
from django.db import models
from typing import ClassVar
from rebac.mixins import FGAModelSyncMixin
from rebac.structs import FGAModelConfig, FGAParentConfig, FGACreatorConfig

class Document(FGAModelSyncMixin, models.Model):
    title = models.CharField(max_length=255)
    folder_id = models.CharField(max_length=255)
    creator_id = models.CharField(max_length=255)

    fga_config: ClassVar[FGAModelConfig] = FGAModelConfig(
        object_type="document",
        parents=[
            FGAParentConfig(
                relation="folder",
                parent_type="folder",
                local_field="folder_id"
            )
        ],
        creators=[
            FGACreatorConfig(
                relation="editor",
                local_field="creator_id"
            )
        ]
    )
```

### 2. Securing API Views (`FGAViewMixin`)

Secure your DRF endpoints instantly using simple, declarative dictionary configurations. No complex permission classes required. `FGAViewMixin` handles queryset filtering (lists), parent checks (creation), and object checks (updates/deletes).

```python
from rest_framework import viewsets
from rebac.mixins import FGAViewMixin
from rebac.structs import FGAViewConfig
from .models import Document
from .serializers import DocumentSerializer

class DocumentViewSet(FGAViewMixin, viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    fga_config = FGAViewConfig(
        object_type="document",
        read_relation="can_read_document",
        update_relation="can_update",
        delete_relation="can_delete",

        # Parent-Level Authorization for Creation (POST)
        # Verifies the user has permission on the parent scope before allowing creation
        create_parent_type="folder",
        create_parent_field="folder_id",
        create_relation="can_add_items"
    )
```

### 3. Frontend Integration (`FGAPermissionSerializerMixin`)

Inject FGA evaluations directly into your API responses so your frontend knows exactly which action buttons to render. The mixin utilizes advanced custom list serializers to prevent N+1 queries, batching all checks into a single OpenFGA network request.

```python
from rest_framework import serializers
from rebac.serializers import FGAPermissionSerializerMixin
from .models import Document

class DocumentSerializer(FGAPermissionSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = Document
        # The mixin automatically injects "_permissions" into this tuple!
        fields = ("id", "title", "folder_id")

        # Declarative rules processed by the mixin
        fga_object_type = "document"
        fga_permissions = ("can_update", "can_delete")
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
    'fga-outbox-sweeper': {
        'task': 'rebac.tasks.process_fga_outbox_batch',
        'schedule': crontab(minute='*/5'), # Sweep the Outbox every 5 minutes
    },
}
```
