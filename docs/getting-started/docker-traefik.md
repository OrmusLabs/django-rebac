# Enterprise Deployment with Traefik

To build a "Defense in Depth" topology, we utilize two distinct Docker networks (`public_net` and `private_net`) and Traefik v3.

The coolest part of this setup is that the Internal Gateway (port 8080) is completely hidden from the outside world; only the BFF (Next.js) is physically allowed to speak to it.

## Production `docker-compose.yml`

This configuration uses `internal: true` on the private network to drop all packets originating from outside the Docker host, achieving True Enterprise Zero Trust.

```yaml
version: '3.8'

networks:
  public_net:
    driver: bridge
  private_net:
    driver: bridge
    internal: true # Drops outside packets

services:
  # 1. THE EDGE & INTERNAL GATEWAY (Traefik v3)
  traefik:
    image: traefik:v3.6.9
    command:
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"     # Public Edge
      - "--entrypoints.internal.address=:8080" # Internal Gateway
    ports:
      - "80:80" # 8080 is NOT exposed to the host!
    networks:
      - public_net
      - private_net
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock:ro"

  # 2. CENTRAL AUTH (The Authority)
  auth-service:
    build: ./django_auth
    networks:
      - private_net
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.auth.rule=PathPrefix(`/auth`)"
      - "traefik.http.routers.auth.entrypoints=internal"
      # The V3 ForwardAuth Middleware
      - "traefik.http.middlewares.jwt-auth.forwardauth.address=http://auth-service:8000/auth/verify"
      - "traefik.http.middlewares.jwt-auth.forwardauth.authResponseHeaders=X-User-Id,X-User-Email"

  # 3. MINI-APP A (The Protected Resource)
  miniapp-a:
    build: ./django_miniapp
    networks:
      - private_net
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.miniapp.rule=PathPrefix(`/miniapp-a`)"
      - "traefik.http.routers.miniapp.entrypoints=internal"
      # The Gatekeeper: Forces Traefik to run jwt-auth before letting traffic in
      - "traefik.http.routers.miniapp.middlewares=jwt-auth"
```

## The Request Flow
1. **The Hand-off:** Next.js fires an internal request to `traefik:8080/miniapp-a/`.
2. **The Interception:** Traefik hits the `jwt-auth` middleware wall and asks the Auth Service (`/auth/verify`) if the token is good.
3. **The Resolution:** Auth returns a `200 OK` and attaches user headers (like `X-User-Id`). Traefik maps that header and lets the request through to the Mini-App.
