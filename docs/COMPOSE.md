# Compose deployment behind SWAG

`docker-compose.yml` runs the frontend, Cadevil, a one-shot migration job, PostgreSQL and Redis. The frontend is the only service attached to the existing external `swagnet` network. All services also use a dedicated private network, which is internal by default. No service publishes a host port. SWAG terminates HTTPS and connects to the frontend's network alias `cadevil` on port 8080; the frontend proxies HTTP and WebSocket traffic to Bolt.

Container requests close Django database connections in the same thread that runs ORM work, including failed requests. WebSocket permission refreshes have their own connection boundaries. After PostgreSQL returns from a restart, subsequent requests open fresh connections; requests during the outage can still fail. Redis caches session reads while PostgreSQL keeps their durable records.

The frontend, PostgreSQL and Redis images use verified official multi-platform digests: Nginx 1.30.5 Alpine, PostgreSQL 18.6 Trixie and Redis 8.4.7 Alpine. The application defaults to the locally built `cadevil:0.15.0` image. Compose does not rebuild or transmit a checkout. Its exact image input list still excludes deployment configuration and every secret file; runtime config files are narrowly bind-mounted read-only. The frontend runs as UID 101, Cadevil as UID 10001, PostgreSQL as UID 999 and Redis as UID 999. Services drop capabilities, use read-only root filesystems and bounded container logs; writable state lives in named volumes or tmpfs.

## Prepare configuration and runtime secrets

Build the application with `make docker-build`. Copy `docker/compose.env.example` to a file outside the checkout, such as `/secure/cadevil/compose.env`, and replace the public hostname/origin and secret file paths. This environment file contains deployment metadata and paths, never key/password values.

Generate four independent random secrets outside the checkout. A protected parent directory is important because file-based [Compose secrets are single-file bind mounts](https://docs.docker.com/compose/how-tos/use-secrets/): their host ownership/mode is retained. The nonroot containers need to read the mounted files. Setting Compose `uid`, `gid` or `mode` on a file source does not remap these permissions. For a deployment owned by the current host user, use a parent directory with mode 0700 and secret files with mode 0444. Other host users cannot traverse that protected parent; only individually granted secret files are mounted in each container.

```sh
mkdir -p /secure/cadevil/secrets
chmod 0700 /secure/cadevil/secrets
python -c 'import secrets; print(secrets.token_hex(48))' > /secure/cadevil/secrets/django_secret_key
python -c 'import secrets; print(secrets.token_hex(32))' > /secure/cadevil/secrets/postgres_password
python -c 'import secrets; print(secrets.token_hex(32))' > /secure/cadevil/secrets/postgres_admin_password
python -c 'import secrets; print(secrets.token_hex(32))' > /secure/cadevil/secrets/redis_password
chmod 0444 /secure/cadevil/secrets/django_secret_key /secure/cadevil/secrets/postgres_password /secure/cadevil/secrets/postgres_admin_password /secure/cadevil/secrets/redis_password
```

Use a writable secure directory appropriate to your host; `/secure` is an example. Do not put secrets in the repository, Docker build arguments, image layers, URLs, Compose environment values or shell command arguments. The application reads `SECRET_KEY_FILE`, `DATABASE_PASSWORD_FILE` and `REDIS_PASSWORD_FILE` at runtime. PostgreSQL uses its official `POSTGRES_PASSWORD_FILE` convention for a separate bootstrap administrator password, granted only to the database container. Its initialization script creates a dedicated application role/database, with no superuser, role-creation, database-creation, replication or row-security-bypass privileges. The application owns its database so migrations can create its tables. `CADEVIL_DB_USER` and `CADEVIL_DB_NAME` default to `cadevil`; use lowercase identifiers of at most 63 characters, avoiding `postgres`, `pg_*` and template database names. Redis hashes its runtime password into a mode-0600 ACL file on tmpfs and passes only that filename to the server; its health check authenticates without a password command argument.

Docker administrators can read container secrets. File-based Compose secrets rely on host filesystem protection and are not an encrypted secret store. Use your deployment's secret manager when stronger lifecycle/rotation controls are needed.

## Connect SWAG and start

The `swagnet` network must already exist and SWAG must be attached to it. Compose treats it as external and leaves it in place on shutdown:

```sh
docker network inspect swagnet
docker compose --env-file /secure/cadevil/compose.env config --quiet
docker compose --env-file /secure/cadevil/compose.env up --detach
```

Configure SWAG's HTTPS virtual host to proxy to `http://cadevil:8080`, preserve the public Host and WebSocket upgrade headers, and allow the desired upload size and long-running response timeout. The inner frontend explicitly forwards WebSocket Upgrade/Connection, retains Host for session/CSRF/Origin checks, and uses 900-second upstream read/send timeouts. Docker DNS is refreshed so replacing the application container does not leave the frontend on an old IP.

The frontend overwrites forwarded scheme/port headers with HTTPS/443, because this listener is for TLS-terminated SWAG traffic. It replaces forwarded client headers instead of trusting arbitrary incoming values. Only trusted peers should have access to `swagnet`; do not publish the frontend's HTTP port directly to untrusted clients. App settings enable forwarded HTTPS trust for this controlled proxy hop, while HTTPS redirects, secure cookies and HSTS stay enabled. The frontend caps request bodies at 64 MiB; app upload limits may be smaller and still apply.

PostgreSQL and Redis must become healthy before the one-shot migration job runs. The application waits for successful migration completion and healthy backend services; the frontend then waits for a healthy application. The migration job does not restart indefinitely after an error. Inspect its failure before rerunning deployment. Create the initial administrator explicitly:

```sh
docker compose --env-file /secure/cadevil/compose.env run --rm app python manage.py createsuperuser
docker compose --env-file /secure/cadevil/compose.env ps --all
docker compose --env-file /secure/cadevil/compose.env logs --tail 100 migrate app frontend
```

The frontend health check is local Nginx liveness; the app health check additionally checks database connectivity. Redis health requires authentication and PostgreSQL health checks TCP readiness, so its temporary initialization server cannot release the migration gate early. These checks do not prove third-party services, weather simulations or every workflow is available. Development MCP plugins and subprocesses remain absent/disabled in the production image.

Frontend access logs contain status, method, duration and the upstream request ID without request URLs/query strings or client identities. PostgreSQL [logging settings](https://www.postgresql.org/docs/18/runtime-config-logging.html) disable ordinary statement logging, suppress error statement/parameter logging and use terse error messages. This reduces SQL/data exposure; it does not apply the application's Python redactor to database or proxy messages and is not an absolute redaction guarantee.

## Private network and online providers

`CADEVIL_PRIVATE_INTERNAL=true` is the default. It keeps backend services on an internal network and blocks their ordinary internet egress, including online utility/location lookups and SMTP. Cached/provider fixtures still work. If this deployment must perform live provider requests, set `CADEVIL_PRIVATE_INTERNAL=false` in the external environment file and recreate the deployment network. Services remain attached only to the private deployment network and have no published ports; the change enables outbound NAT. Use a controlled egress proxy/firewall when destinations must be restricted. Do not attach PostgreSQL, Redis or the application to `swagnet` to provide egress.

The external network name can be overridden with `CADEVIL_SWAG_NETWORK` for an isolated QA deployment. Production defaults to `swagnet`. Each deployment's named volumes and private network are scoped to its Compose project name. The external alias `cadevil` assumes one Cadevil frontend per SWAG network; use a distinct alias/config override when deploying multiple instances on the same network.

## Persistence, upgrades and backups

Version 0.15.0 introduces plugin-owned BIM persistence with a new `bim_model_manager` app label/table prefix and fresh authentication/BIM migration histories. It requires an empty Cadevil application database; it is not an in-place upgrade from 0.14. Retain a database dump and file backup separately, initialize the new schema, recreate administrator/login access, and import the models or packages to use in the new service. Scope this reset to the Cadevil application database and its associated application state; other databases, deployments, shared networks and runtime secrets are independent. Backups remain available for recovery outside the new schema.

The application data volume contains uploads, signed plugin packages, generated caches and its admin log store. PostgreSQL holds application accounts/model records; Redis has its own cache volume and AOF persistence. Back up both PostgreSQL and the app volume; Redis can be rebuilt as a cache. Logs and database state are never build inputs.

For [PostgreSQL 18's official image](https://github.com/docker-library/docs/tree/master/postgres#pgdata), the named volume is mounted at `/var/lib/postgresql` and `PGDATA` is `/var/lib/postgresql/18/docker`. Mounting only the old `/var/lib/postgresql/data` path would miss the new layout. Pin and review major database upgrades separately; changing the tag does not upgrade an existing database's data format. PostgreSQL's init variables apply only to a new, empty volume. Rotating its password file alone does not update an existing database role password; perform a coordinated database credential rotation before restarting dependent services. Redis loads its configured runtime password at restart, so rotate its file and application together.

For a compatible application release, build the reviewed new image, update `CADEVIL_IMAGE`, back up state, stop the frontend/app to drain existing workers, then run `docker compose up --detach`. The migration service applies schema changes before new web workers start. Stop the deployment while retaining data with:

```sh
docker compose --env-file /secure/cadevil/compose.env down
```

Keep named volumes for ordinary restarts/upgrades. The external SWAG network is never removed by this deployment. Resource defaults are two application CPUs/two HTTP workers/two IFC geometry threads, a 2 GiB app limit, bounded backend resources and a 256 MiB Redis keyspace. Adjust CPU, worker/thread counts and memory together for the building sizes and workload you deploy.
