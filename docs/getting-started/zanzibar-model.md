

# The Zanzibar Authorization Architecture

Our system is not just a simple Auth Server; it is a full **Authorization Platform Architecture** inspired by Google Zanzibar and utilized by platforms like GitHub, Google Docs, and Slack.

## RBAC-over-ReBAC Pattern
The architecture relies on Relationship-Based Access Control (ReBAC). Roles are inherently scoped to specific resources:

* `user:alice` -> `admin` -> `organization:acme`
* `user:bob` -> `viewer` -> `project:alpha`


## Advanced Platform Features

**1. Multi-Tenant Authorization Stores**
Every registered Mini-App operates within a strict isolation boundary, possessing its own independent `openfga_store_id`. This mirrors the multi-tenant architecture seen in Google Cloud projects.

**2. Policy-as-Data Validation**
Authorization rules are not hardcoded in views. Each mini-app defines an assignable roles manifest. The system validates assignments against this manifest, providing domain invariants that enforce security at the model level.
