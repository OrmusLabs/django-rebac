# Installation & Setup

## 1. Install the Package

Install the package via pip or uv:

```bash
pip install django-rebac django-environ django-celery-beat
```
or
```bash
uv add django-rebac django-environ django-celery-beat
```

Add it to your `INSTALLED_APPS` and configure the Traefik middleware in your `settings.py`:

```python
import environ

env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False)
)

INSTALLED_APPS = [
    # ... your other apps ...
    'django_celery_beat', # for outbox pattern
    'rebac',
]

MIDDLEWARE = [
    # ...
    # Dynamically maps gateway headers to request attributes
    'rebac.middleware.GatewayIdentityMiddleware',
]


# Celery Configurations
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")

# It is standard practice to use the same Redis instance for results
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
```

Run migrations to create the Outbox table in your database:

```bash
python manage.py migrate rebac
```

## 2. Configuration

Configure the package by adding the `REBAC_CONFIG` dictionary to your `settings.py`.

```python
REBAC_CONFIG = {
    # Specify the active backend adapter
    "BACKEND": "rebac.backends.openfga.client.OpenFGABackend",

    # Backend-specific options go here!
    "BACKEND_OPTIONS": {
        "STORE_ID": "01H...XYZ",
        "API_URL": "http://localhost:8080",
    },

    "BATCH_SIZE": 50,
    "MAX_RETRIES": 5,

    "REQUEST_HEADER_MAPPINGS": {
        "X-User-Id": "rebac_user",
        "X-Context-Org-Id": "rebac_tenant",
    },
    "REBAC_USER_ATTR": "rebac_user",
}
```

## 3. Celery Configuration

Because this package uses the Transactional Outbox pattern, you must have Celery configured in your project to process the queued network requests.

The background task (`process_rebac_outbox_batch`) is automatically triggered upon a successful database commit. However, as a fail-safe against broker crashes, you should configure a Celery Beat sweeper to run periodically:

```python
# <project_name>/celery.py
import os

from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "<project_name>.settings")

app = Celery("<project_name>")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix in settings.py.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
```

and

```python
# <project_name>/__init__.py

# This ensures the Celery app is always imported when Django starts.
# It guarantees that shared_task decorators will use this app instance.
from .celery import app as celery_app

__all__ = ("celery_app",)
```

And for Celery Beat add this task in the `settings.py`:
```python
CELERY_BEAT_SCHEDULE = {
    "fga-outbox-sweeper": {
        "task": "rebac.tasks.process_rebac_outbox_batch",
        "schedule": 300.0,  # Sweep the Outbox every 5 minutes
    },
    ...
}
```
