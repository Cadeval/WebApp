# Plugin Store and signed browser packages

Authenticated users can browse `/plugins/store/`, create signing keys at `/plugins/keys/`, and publish packages signed with their own active key. Administrators review the downloadable package and enable it. Uploaded Python code is not installed or executed. Existing installed Python plugins retain their separate server installation workflow; administrators can continue uploading single JS/WASM files.

## Publish a package

1. Create a named Ed25519 key in the web interface. The browser encrypts its private PKCS8 key using AES-256-GCM and a passphrase derived with PBKDF2-SHA256 (600,000 iterations). Save the downloaded key JSON and passphrase. Neither private key nor passphrase is transmitted to the server. Registration requires a signature of a session-bound, one-use challenge, expiring after ten minutes.
2. Download the example package and standalone signing CLI from the keys page. The CLI uses Python and `cryptography`; `uv run --with cryptography` can supply the dependency.
3. Sign locally, choosing a new output file:

   ```sh
   uv run --with cryptography cadevil_sign.py sign plugin.zip --key your-key.cadevil-key.json --output plugin.signed.zip
   ```

   The CLI prompts for the passphrase and refuses to overwrite the input or an existing output. It supports `.zip`, `.tar`, `.tar.gz`/`.tgz`, and `.tar.xz`/`.txz` outputs.
4. Upload the signed archive. Its manifest supplies the plugin id, name, description and version. It remains disabled until an administrator reviews and enables it. Enabled plugins appear in the Configuration Editor.

## Package format

The archive must contain `plugin.json` at its root, plus browser worker/module files, optional WASM and text documentation. Example manifest:

```json
{
  "id": "publisher.calculator",
  "name": "Calculator",
  "version": "1.0.0",
  "api_version": "1.0",
  "description": "Doubles the supplied value.",
  "type": "javascript",
  "entrypoint": "worker.js"
}
```

JavaScript module workers receive `{type: "initialize"}` and reply `{type: "ready"}`. On `{type: "run", value: number}` they reply `{type: "result", value: number}`. Relative module imports within the package are supported. WASM packages declare `type: "wasm"` and export `calculate(number)`.

The CLI creates root `signature.json` containing the format `cadevil-plugin-signature-v1`, algorithm `Ed25519`, public-key SHA-256 fingerprint `key_id`, and base64 signature. The signed payload is ASCII JSON with sorted keys and compact separators:

```json
{"context":"cadevil-plugin-package-v1","files":{"plugin.json":"SHA256_HEX","worker.js":"SHA256_HEX"}}
```

All file bytes, including manifest and documentation, are hashed. Only `signature.json` is excluded. Changing, adding, or removing a file invalidates the signature. Compression changes do not: validated TAR packages are normalized to private ZIP storage while retaining the original archive format and hash.

The web upload limit is 2 MiB. Archives may contain at most 32 entries, 8 MiB expanded total and 2 MiB per file. Links, devices, sparse files, traversal, encrypted ZIPs, duplicate filenames, unsupported contents and excessive expansion are rejected. Files are read within their archive, never extracted to the filesystem. Executable assets require authentication, an enabled plugin and an active signing key; file hashes are checked again when served. Assets are not exposed through public media URLs.

## Revoke a key

Owners and administrators may revoke a public key. Linked packages are disabled, further uploads using that key are rejected, and asset access is blocked. Revocation cannot recover a lost private key or remove code already loaded in another browser worker. Create a new key to publish future packages. Signatures prove publisher identity and integrity, not code safety: browser workers are not a complete security sandbox, and administrators must review code before enabling it.

## OpenZL

OpenZL compresses typed data or byte streams; a multi-file package would need a container such as TAR inside the compressed stream. No OpenZL decoder is installed or integrated here. `.zl` uploads receive a clear unsupported-format message. Repack the file contents into ZIP/TAR/gzip/xz and sign them. The signature protocol is independent of the container, leaving room for a bounded, tested OpenZL decoder later. Primary documentation: https://openzl.org/getting-started/quick-start/ and https://openzl.org/api/py/decompress/.
