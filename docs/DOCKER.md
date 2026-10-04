# Container image

The image runs the native Bolt production server on port 8000 as UID/GID 10001. It includes locked application wheels, public assets and the bundled WASM binaries. Python 3.14.6 Debian Trixie slim and uv 0.12.22 are pinned by multi-platform OCI digest. The reviewed Linux arm64 and amd64 wheels support these platforms; the image uses glibc, not Alpine/musl.

## Build without host files

`docker/image-files.json` is the exact, reviewed input list. `.dockerignore` is generated from it and denies all other paths, including future files under allowed source directories. No broad source glob is an input. The archive helper additionally rejects symlink files and parents, traversal, credentials, tests, private models, databases, logs, development bridges and source archives. Only the two named public controlled demo IFC fixtures and the six public demo GLBs are allowed; no host IFC directories are copied.

Build using a context archive outside the checkout so no directory walk is sent to Docker:

```sh
make docker-build
```

This creates the local `cadevil:0.14.1` image with BuildKit SBOM and minimal
provenance attestations. The helper removes its temporary archive after the
build. For a specific platform or tag, run
`uv run --locked --no-sync python scripts/build_docker.py --platform linux/amd64 --tag cadevil:0.14.1`.
Attestations require a compatible BuildKit builder and containerd image store;
the tested Docker Desktop installation has both. No registry push is performed.

The explicit archive commands remain available:

```sh
python docker/context.py
python docker/context.py --archive /tmp/cadevil-context.tar
docker build --platform linux/arm64 --tag cadevil:0.14.1 - < /tmp/cadevil-context.tar
```

Use `--platform linux/amd64` for an x86-64 host. Docker Desktop must be running. A regular `docker build .` uses the same exact `.dockerignore`, and the builder verifies the received source list before collecting static assets. Build context controls, uv, dependency locks, the static collector and packaging validator remain in intermediate stages. No compiler, Node modules, local virtual environment, test files, development SBOM, MCP subprocess programs, `.env`, Git history, keys, uploaded plugins or user data are copied into the runtime. `pytest` remains an application dependency because IfcOpenShell EXPRESS validation imports it; dependencies may contain their own upstream testing modules.

When an intended runtime file is added or removed, review and edit the manifest, then regenerate and check:

```sh
python docker/context.py --write-ignore
python manage.py test tests.test_docker_packaging --settings=tests.passport_test_settings
python scripts/check_docker_context.py
```

The input archive has normalized public file modes and metadata. The Docker builder makes every curated source/public file readable as UID 10001, including demo GLBs whose host permissions are restrictive. Application code remains owned by root and is not writable by the server. Base image contents and downloaded distribution metadata are separately inventoried in an image SBOM; the repository's runtime SBOM is the dependency audit, not an inventory of Debian packages. Digest pins and locked wheels constrain inputs; changing Debian repositories still means the whole build is not claimed to be byte-for-byte reproducible.

## Initialize persistent state

Keep runtime configuration outside the checkout and image, for example `/secure/cadevil/runtime.env`, protected with host permissions 0600. Supply a random `SECRET_KEY` of at least 50 characters, an explicit host allowlist and HTTPS origins:

```dotenv
SECRET_KEY=GENERATE_A_LONG_RANDOM_KEY_FOR_THIS_DEPLOYMENT
ALLOWED_HOSTS=cadevil.example.org
CSRF_TRUSTED_ORIGINS=https://cadevil.example.org
CADEVIL_TRUST_PROXY_HTTPS=true
CADEVIL_HTTP_PROCESSES=2
IFC_GEOMETRY_THREADS=2
```

The placeholder key above is not a production credential; generate a random one before running. Alternatively inject a secret readable by UID 10001 and set `SECRET_KEY_FILE=/run/secrets/cadevil_secret_key`. Set exactly one of `SECRET_KEY` and `SECRET_KEY_FILE`. The file is read only at runtime; no secret build argument or baked default is used.

Create a named data volume and apply migrations once before starting workers:

```sh
docker volume create cadevil-data
docker run --rm --init --read-only --tmpfs /tmp:rw,nosuid,size=256m \
  --mount type=volume,src=cadevil-data,dst=/app/data \
  --env-file /secure/cadevil/runtime.env \
  cadevil:0.14.1 python manage.py migrate --noinput
docker run --rm --init -it --read-only --tmpfs /tmp:rw,nosuid,size=256m \
  --mount type=volume,src=cadevil-data,dst=/app/data \
  --env-file /secure/cadevil/runtime.env \
  cadevil:0.14.1 python manage.py createsuperuser
```

`/app/data` holds the SQLite database, uploads/private plugin archives, log store and generated caches. It starts empty; no local database or accounts are bundled. Back up the volume separately. For an existing deployment, restore its database and files into the volume with UID/GID 10001 permissions and then apply migrations. Do not mount production state into a build. Automatic migration on every web-worker startup is deliberately avoided.

The standalone defaults use SQLite; locked Psycopg and Redis clients also support PostgreSQL and shared caching. [Docker Compose](COMPOSE.md) provisions those services on a private network and connects a frontend proxy to external `swagnet`. Mounted `DATABASE_PASSWORD_FILE` and `REDIS_PASSWORD_FILE` provide service credentials without putting passwords in Compose environment values. PostgreSQL retains session records while Redis caches session reads. OpenStudio is optional and is not bundled; energy simulations which require it need a separately configured runtime installation and weather input.

## Run behind HTTPS

```sh
docker run --detach --name cadevil --init --read-only \
  --tmpfs /tmp:rw,nosuid,size=256m \
  --mount type=volume,src=cadevil-data,dst=/app/data \
  --env-file /secure/cadevil/runtime.env \
  --publish 127.0.0.1:8080:8000 \
  cadevil:0.14.1
```

Terminate TLS at a reverse proxy and proxy HTTP/WebSocket traffic to this private backend. When `CADEVIL_TRUST_PROXY_HTTPS=true`, the application trusts `X-Forwarded-Proto: https`. Enable it only if the proxy strips client-supplied forwarded headers and supplies its own. Keep the backend reachable only by that proxy; do not expose a trusted-header backend directly to clients. The proxy must forward the configured Host and support WebSocket upgrades. HTTPS redirects, secure cookies and HSTS remain enabled.

Django admin assets are collected during the build. Bundled IFC/BIM assets stay in their owning plugin's `static/` directory, registered by the [plugin manager's Django adapter](PLUGIN_RESOURCES.md). Bolt 0.11.1's [native static server](https://github.com/dj-bolt/django-bolt/blob/v0.11.1/src/server.rs#L368-L429) searches both `STATIC_ROOT` and `STATICFILES_DIRS` in production, so first-party public assets remain in their original directory without a second copy of the large demo models. Private media remains accessible only through owner-checked routes. Debug mode and all MCP plugins are disabled by container settings regardless of an environment variable opt-in; `/dev/mcp` has no production route.

The fixed public `/healthz` checks HTTP worker dispatch and database connectivity with `SELECT 1`, returning only `ready` or `unavailable`. It is the sole HTTPS redirect exception for internal probes. It does not validate pending migrations, simulation dependencies or third-party service availability. The built-in health check runs every 30 seconds with a 60-second startup allowance. Request/log metadata contains no model attributes or credentials; structured diagnostic output goes to standard error and the bounded admin log store remains under `/app/data`.

```sh
docker inspect --format '{{.State.Health.Status}}' cadevil
docker logs cadevil
docker stop cadevil
```

Adjust `CADEVIL_HTTP_PROCESSES` and `IFC_GEOMETRY_THREADS` together to respect the CPU/memory limit; defaults are two each. `CADEVIL_HTTP_MAX_RSS` controls each HTTP worker's restart threshold in MiB (default 512). The deployment should also set Docker CPU/memory limits. These workers restart on bounded lifetime or failure and inherit Docker's stop signal through the entrypoint's `exec`.
