# tests/test_settings.py
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "test-secret-key-do-not-use-in-production"
DEBUG = True

# We only install the bare minimum required for the ORM and our app to function
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rebac",
    # If you create mock models for testing (e.g., a fake Document model),
    # you would register that test app here.
]

# Use an ultra-fast, ephemeral in-memory SQLite database for the test suite
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Provide mock FGA Configuration to satisfy the conf.py validators
REBAC_CONFIG = {
    "BACKEND_OPTIONS": {
        "STORE_ID": "01H0H0H0H0H0H0H0H0H0H0H0H0",
        "API_URL": "http://localhost:8080",
    }
}

# ==========================================
# CELERY TEST CONFIGURATION
# ==========================================
# Force Celery to execute tasks synchronously in the same thread.
# This prevents the need for Redis or RabbitMQ during testing.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_STORE_EAGER_RESULT = True
