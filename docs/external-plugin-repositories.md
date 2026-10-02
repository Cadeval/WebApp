# Administrator-approved external plugin repositories

This is an implementation proposal. Repository fetching, remote catalogs and release updates are not implemented by the current plugin store. The first implementation should import approved browser packages into private local storage; users choose locally approved releases for their own workflows.

## Existing boundaries to retain

The current store accepts signed ZIP, TAR, gzip/TAR and xz/TAR archives. `PluginUploadForm` normalizes them, `validate_package()` validates their structure, and `verify_package_signature()` checks Ed25519 signatures over the hashes of every package file except `signature.json`. Uploaded packages contain browser JavaScript/WASM and documentation; they cannot install Python, shell commands or MCP servers. The limits are 2 MiB compressed, 32 archive entries, 8 MiB expanded and 2 MiB per member. OpenZL remains unsupported.

`PluginRecord.enabled` is the administrator's approval and emergency disable switch. It must stay separate from a user's install/selection state. Effective availability requires administrative approval, an active trusted signing key, matching debug/production compatibility, and the requesting user's workflow selection. Signatures establish integrity and publisher identity; code review is still required.

## Administration and data model

Administrators configure a `PluginRepository` containing a name, fixed HTTPS catalog URL, exact allowed origin/path prefix, enabled state, permitted package namespaces, pinned catalog signing-key fingerprints, sync status, last accepted catalog sequence and expiry. Repository authentication, if needed, uses a server-side secret reference, never credentials embedded in a URL. Regular users cannot add repositories, change trust or submit arbitrary fetch URLs.

Add `TrustedPublisherKey` for external publisher public keys, fingerprints, allowed namespaces, repository association and revocation. Register pins through administrator review and an independent fingerprint check. Do not invent local user accounts to satisfy the existing `PluginSigningKey.owner` requirement. Keep user-created keys and their proof-of-possession registration unchanged; extract a shared signature verifier accepting an explicitly resolved trusted public key.

Add immutable `PluginRelease` records with plugin ID, version, API version, compatibility, publisher fingerprint, signed file-set digest, original archive hash, canonical stored ZIP hash, private artifact, repository/catalog provenance and review state. Approval records identify reviewer, time, reviewed digest and notes. Use a unique `(plugin_id, version)` constraint and reject a second byte identity for that pair. Plugin IDs must belong to an administrator-approved publisher namespace and must not collide with bundled plugins or another publisher.

The current unique `PluginRecord.plugin_id` represents a plugin, not its version history. Add releases beneath that identity rather than bypassing the duplicate-ID check or replacing its artifact in place. User/workflow selections reference an exact approved release. Preserve the existing per-user selection API and migrate its release reference when versioning is introduced.

## Signed catalog contract

Use a bounded JSON document with a separately signed catalog envelope. Its signature covers the canonical `signed` object with the domain separator `cadevil-plugin-catalog-v1`. Define canonicalization and accepted fields in the protocol and provide a reference signing tool; reject duplicate JSON keys and unsupported schema versions. Catalog keys and package publisher keys are distinct trust decisions.

```json
{
  "signed": {
    "format": "cadevil-plugin-catalog-v1",
    "repository": "example-publisher",
    "sequence": 42,
    "issued_at": "2026-10-03T10:00:00Z",
    "expires_at": "2026-10-10T10:00:00Z",
    "releases": [{
      "id": "publisher.calculator",
      "version": "1.0.0",
      "api_version": "1.0",
      "compatibility": "both",
      "type": "javascript",
      "publisher_key_id": "<64-character SHA-256 fingerprint>",
      "package_digest": "<SHA-256 of canonical signed file-set payload>",
      "archive": {
        "path": "packages/calculator-1.0.0.signed.zip",
        "sha256": "<SHA-256 of downloaded archive bytes>",
        "size": 12345
      }
    }]
  },
  "signatures": [{
    "algorithm": "Ed25519",
    "key_id": "<pinned catalog-key fingerprint>",
    "signature": "<base64 signature>"
  }]
}
```

Catalog entries describe discovery; the validated, signed `plugin.json` supplies executable package metadata. Require both representations to agree. Package paths are relative to the configured repository root and cannot change the host or escape its path prefix. No dependency installation, hooks, executable URLs or subprocess declarations belong in this schema.

## Fetch, validation and review

1. An administrator syncs the configured catalog through a bounded background job. Cap catalog size, release count, timeouts and concurrent downloads. Fetch only HTTPS with certificate verification. Disable redirects, ambient proxy configuration and automatic retries to alternate hosts. Never forward user cookies or application authorization headers.
2. Apply an exact origin allowlist and reject credentials, fragments, encoded path traversal and non-HTTPS URLs. Resolve and validate all IPv4/IPv6 results against loopback, private, link-local, multicast, unspecified and reserved ranges. Bind the connection to a validated address while retaining the correct TLS hostname, or enforce equivalent restrictions in an egress proxy. Validate again on each new connection. A DNS precheck followed by an unrestricted second lookup does not close rebinding. Development localhost test fixtures require an explicit DEBUG-only transport override.
3. Verify the catalog signature with pinned keys, then check repository ID, expiry, sequence and schema. Persist the highest accepted sequence; reject rollback and expired catalogs. An equal sequence is acceptable only for an identical signed payload. Expiry blocks new synchronization/install/update actions without discarding already approved local releases.
4. Stream each candidate archive into bounded temporary storage; enforce declared size and the 2 MiB cap regardless of `Content-Length`. Check its raw SHA-256 before normalization. Reuse the archive validator, package signature protocol and API/environment checks. Match publisher namespace, catalog metadata and file-set digest. A valid signature does not approve the package.
5. Cache the normalized archive privately and present an administrator review screen with source, publisher fingerprint, signature/integrity results, manifest, member list, code/documentation download and differences from the previous release. Render remote descriptions as escaped text. Approval applies to the exact immutable digest, with an audit event. Failed imports leave no visible installable record or orphaned private artifact.

Store the raw archive hash separately from the signed file-set digest and normalized ZIP hash. TAR normalization can change ZIP bytes across imports; release identity should use the stable signed file-set digest, not assume normalized container hashes are reproducible.

## User installation and workflow selection

The store lists approved, compatible, non-revoked releases from enabled repositories. An authenticated user's Install/Select POST accepts a local release identifier, never a URL; it creates or updates only that user's workflow selection. Package fetching and review happen beforehand, so installation does not import arbitrary remote content on a user's request. Switching or removing a selection cannot enable/disable the plugin globally or affect another user.

Use one request-aware availability resolver for store actions, navigation, editor contributions, plugin pages and every asset request. Apply active-user authentication, ownership/workflow checks and CSRF protection to mutations. Keep existing private asset storage, file-hash checks and response CSP. Store the selected plugin ID/version/digest in calculation/report provenance so prior results remain reproducible.

The current `_uploaded_editor_items()` is global and receives no user, while `package_asset()` currently grants any authenticated user access to a globally enabled upload. Both require the per-user resolver; filtering only the menu would leave the asset route open. Installed server plugins may remain globally discovered, but user-facing availability still needs workflow checks.

## Updates, revocation and environment boundaries

New versions create new unapproved releases. After review, users receive an explicit update choice; existing selections remain pinned. Never silently replace code under a selected version. A publisher-key revocation or administrator quarantine blocks selection and assets for all affected releases. Removing a repository from the active allowlist blocks its releases until administrators explicitly approve retaining them as local packages. Repository outages alone leave approved cached versions usable. Key rotation needs an administrator-approved new pin; a remote catalog cannot grant itself new trust.

Notify active pages of selection changes, quarantine and revocation and terminate affected workers. Requests must recheck availability. Already loaded browser code cannot be reliably recalled by denying its next asset request; preserve that limitation in the operational documentation.

External repositories distribute only the existing browser package types. They cannot declare a server entry point or MCP process. MCP remains installed through the administrator-controlled development plugin pipeline, requires DEBUG, and is excluded from production launch and per-user workflow selection. Browser-package compatibility labels cannot override that boundary.

## Delivery sequence and acceptance checks

Implement immutable releases/trust records and shared signature verification first; then bounded administrator-only catalog sync and review; finally store installation/update UI integrated with per-user selection. Start with manual administrator sync and one catalog format. Add scheduled sync only when requested; adopt a full update framework if multi-party key rotation/delegation requires it.

Tests should cover non-staff repository configuration denial; cross-user selection/asset isolation; forged catalogs and keys; namespace collision; expired/replayed catalogs; private-IP/DNS-rebinding/redirect targets; truncated or oversized responses; archive traversal/expansion; signature/catalog/hash mismatch; version mutation; review gating; concurrent duplicate installs; revocation; pinned-version updates; and production exclusion of all MCP declarations.

The networking restrictions follow the [OWASP SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html). Sequence and expiration checks are a limited catalog design informed by [The Update Framework specification](https://theupdateframework.github.io/specification/); they do not claim full TUF compatibility or its complete compromise-resistance model.
