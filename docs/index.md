# Django ReBAC

*The ultimate enterprise authorization platform for Django.*

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-4.2%20%7C%205.2-0C4B33)](https://www.djangoproject.com/)
[![Zanzibar](https://img.shields.io/badge/Zanzibar-Ready-8A2BE2)](https://openfga.dev/)

**Django ReBAC** is a declarative, Outbox-pattern ReBAC synchronizer for Django models.

This package automatically translates your Django relational models into ReBAC authorization graph tuples. It guarantees perfect, highly-available synchronization between your local PostgreSQL database and your distributed ReBAC server using the **Transactional Outbox pattern** and **Celery**.

---

## 🚀 Why use this package?

* ⚡ **Zero-Friction Synchronization:** Write standard Django models, and we handle the distributed ReBAC network writes automatically in the background.
* 🛡️ **Declarative Security:** Secure your DRF endpoints instantly using simple, declarative dictionary configurations. No more complex permission classes.
* 🔗 **100% Decoupled:** Change your business security rules (e.g., adding a new `viewer` role) without ever deploying new Python code.
* 🏗️ **Clean Architecture Ready:** Designed to work seamlessly with Repository patterns and Service layers, keeping your Django Views pristine and your business logic testable.

---



## 🗺️ Where to go next?

Ready to get started? Follow our step-by-step guides to wire up enterprise-grade authorization in minutes.

<div class="grid cards" markdown>

-   :fontawesome-solid-rocket: **[Getting Started](getting-started/installation.md)**

    ---

    Install the package, configure Traefik, and run your first ReBAC migration.

-   :fontawesome-solid-sitemap: **[Architecture & Philosophy](architecture/philosophy.md)**

    ---

    Learn about the Zanzibar model, SOLID principles, and the Transactional Outbox pattern.

-   :fontawesome-solid-shield-halved: **[ReBAC Schema Design](schema/design-guide.md)**

    ---

    Master the "Roles vs. Permissions" pattern to decouple your security rules.

-   :fontawesome-solid-book-open: **[Developer Guides](examples/models.md)**

    ---

    Learn how to sync models, secure API views, and handle nested hierarchies safely.

</div>
