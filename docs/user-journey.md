# Workspace navigation and account settings

The canonical Plugins page is `/plugins/manage/`. It combines **My workflow**, the user's selected tools, with **Available tools**, the enabled tools they can add. The navigation contains one Plugins link, User settings, and the user's selected workflow tools. Removing a tool removes its personal selection while retaining saved models, configurations and reports. A selected tool that becomes globally unavailable stays visible in My workflow with a removal action and no Open action.

The landing page offers the demo without authentication. Signed-in users can choose workflow tools or continue with their selected tools. It avoids installation instructions and obsolete lists of planned features. A typical BIM journey is Plugins → add the BIM tool → open the model workspace → upload an IFC model → configure and calculate → inspect the report and linked elements in the viewer.

The old `/plugins/store/` route resolves to the unified Plugins page. The old signing-key page resolves to `/mycelium/settings?section=security`; existing registration, revocation and CLI download routes remain available. Uploaded browser plugins have their own guarded workflow page rather than requiring the BIM tool to be selected.

## User settings

`/mycelium/settings` has three sections:

- **Account:** editable first name, last name, email and theme. Theme choices are Automatic, Light and Dark. Automatic follows the system palette; explicit choices override it.
- **Security:** create signing keys locally in the browser, inspect/revoke public keys and download the signing CLI. Change password is a collapsed panel that remains expanded after submission so validation is visible; changing a password retains the current authenticated session. Private keys are downloaded as encrypted files; the server receives the public key and registration proof. Revocation disables packages signed with that key.
- **Access and capacity:** account role, group memberships, active calculations and the concurrent calculation limit. Authorized staff also see user and group administration links.

Signing-key controls are embedded in the Security section. The `key-registration` JSON element and `data-signing-key-form`, `data-key-controls` and `data-key-download` selectors preserve the browser cryptography contract. Registration continues to return JSON; revocation rerenders the complete Security section. The key-list refresh link can retrieve newly registered public keys without navigating to a separate key-management page.

## Permissions and administration

Personal workflow actions change only the authenticated user's selection. Site-wide discovery, global plugin activation and the full installed-plugin inventory remain staff-only. Publishing a signed archive is available as a collapsed secondary panel on Plugins; regular users publish packages signed by their own registered keys, and site approval determines availability.

`/mycelium/settings/users` requires staff status and the user view permission. Creating accounts additionally requires the add permission; editing or changing active status requires the change permission. The page exposes account information and calculation limits within these grants. Delegated administrators cannot modify privileged accounts or assign privileges they cannot grant. View-only or protected records have no edit controls. Self-deactivation and removal of the last active superuser are rejected by the backend.

`/mycelium/settings/groups` requires staff status and the group view permission, with separate add/change permissions for mutations. Delegated administrators can assign only authorized groups and permissions; protected groups remain view-only. All administrative mutations use CSRF-protected POST requests. Form validation retains submitted values and provides field-linked error summaries.

## HTMX 4 page contract

Local application navigation loads a fragment into `#content-container` with `outerHTML` replacement, preserving the shared page shell. Page-level POST responses also refresh selected-tool navigation out of band. Legacy page aliases resolve to canonical URLs; the server can replace the fragment history URL when appropriate.

The vendored library is the unmodified `dist/htmx.js` from the official `htmx.org` npm distribution, version **4.0.0**. Source provenance is retained in `resources/static/js/htmx.version.txt`, with the distribution license beside the file. Upstream source: <https://github.com/bigskysoftware/htmx/tree/v4.0.0>.

HTMX 4 migration uses explicit `:inherited` attributes for shell-wide headers, boost, target, swap and history defaults, `hx-disable` for pending controls, and colon-delimited lifecycle events. Fragment navigation stays immediately interactive without transition snapshots. Configuration limits requests to same origin and preserves unlimited duration for long IFC calculations. Form validation responses remain actionable; unexpected permission/server failures retain the current page. Script cache versions are updated when the lifecycle integration changes.

Django's native administration, logout, package/CLI/key-file downloads, external sites and third-party tools that explicitly require a full page retain native navigation. Download links opt out of boosting. Hash links for the skip control and field errors also stay native so they move focus within the current document.

## Accessibility and verification limits

The shell supplies a skip link to a focusable main landmark. Section titles, current-section navigation, visible keyboard focus, polite notices, field-linked errors, bounded horizontal table scrolling and responsive workflow cards support keyboard and small-screen use. User settings fragments carry `data-user-theme` so saving a theme can update the shell immediately.

The development-only UI/UX MCP uses `ui-ux-suite` **0.6.1**. Its bounded static audit examined 51 CSS/template files and reported 46 heuristic findings before this journey revision. Those findings guide inspection; they are not a rendered accessibility verdict. CSS overrides, system/explicit palettes, wrapped labels, commented markup and decorative/icon-font elements can produce misleading static findings. The MCP's audit and guidance tools are read-only and do not run on production.

The revised muted help colors are `#545b62` on the light palette and `#c3c7cb` on the dark palette. Calculated contrast is **6.53–6.89:1** on light backgrounds and **8.14–9.72:1** on dark backgrounds. Disabled controls retain readable text rather than reducing the entire control's opacity. These calculations verify those foreground/background pairs only; browser screenshots, keyboard behavior and live state changes still require runtime verification. Template compilation and role/state rendering checks cover regular/staff Plugins, unavailable selections, account/security/access sections, embedded signing controls and POST navigation refresh.
