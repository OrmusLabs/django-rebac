
# Architecture Overview

This package handles authorization seamlessly across microservice boundaries. Here is what happens under the hood when a user interacts with the system.

## The End-User Flow
From the user's point of view, the system is just one giant, fast, cohesive platform. They have no idea they are crossing microservice boundaries.

1. **Authentication (The Gateway):** The user logs into the portal. The central `auth` service verifies the password and issues a Traefik session token.
2. **Global Role Assignment:** An HR Admin assigns the user a role (e.g., `owner` of "Alpha folder") via the central dashboard, which silently writes this rule to ReBAC.
3. **The Action:** The user clicks a link to open a Miniapp and attempts to create a document.
4. **The Verification:** The Miniapp intercepts the click, glances at the hidden `X-User-Id` header, and instantly asks ReBAC if the user is authorized.
5. **The Result:** The document is created, and the user gets a success message in 100 milliseconds.
6. **The Background Magic:** Unbeknownst to the user, the Miniapp's Celery worker whispers to ReBAC to update the graph based on the newly created record.
