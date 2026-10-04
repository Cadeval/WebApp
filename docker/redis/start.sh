#!/bin/sh
set -eu

# Compose mounts the raw password only at runtime. Store its SHA-256 verifier
# in an ephemeral ACL file; no credential appears in the server arguments.
password=$(sed 's/\r$//' /run/secrets/redis_password)
if [ "${#password}" -lt 32 ]; then
    printf '%s\n' 'Redis requires a random password of at least 32 characters.' >&2
    exit 1
fi
password_hash=$(printf '%s' "$password" | sha256sum | cut -d ' ' -f 1)
unset password
umask 077
printf 'user default on #%s ~* &* +@all\n' "$password_hash" > /tmp/cadevil-redis.acl
unset password_hash
exec redis-server \
    --bind 0.0.0.0 --protected-mode yes \
    --aclfile /tmp/cadevil-redis.acl \
    --dir /data --appendonly yes --save '' \
    --maxmemory "${REDIS_MAXMEMORY:-256mb}" --maxmemory-policy allkeys-lru
