# Plugin workspace tabs

BIM Workspace contributes one menu entry at `/plugins/bim/`. Its related tools use URL-backed tabs: Models, Map, References, Reference editor, Material passport and Comparison. Only the selected handler renders; inactive tabs do not prepare models, start viewers, query local-data providers or initialize browser workers.

Tab links have real URLs and use HTMX to replace the shared `content-container` boundary. Each selected URL can be bookmarked, opened directly or restored with browser history. The server retains the full shell for normal requests and HTMX history restoration, and supplies a single content boundary for partial responses.

Existing BIM routes and reverse names remain available. Their pages render inside the same workspace. Viewer and CityJSON details belong to Models; viewer links with `from=map`, location editing and local-data pages belong to Map. Saved passport reports belong to Material passport. Report downloads, geometry, materials and thumbnail endpoints keep their existing response types and access checks. Existing forms keep their POST endpoints, CSRF checks and ownership filters. Inline map lookups retain their smaller result targets.

Authentication and personal workflow availability are checked before workspace dispatch. A disabled or unselected BIM plugin remains unavailable; the tab shell does not grant access to sources or reports. The public `/demo` remains separate and shows no private workspace tabs.

The shared `plugin_workspace` rendering component accepts trusted metadata attached to the request: a name, slug, menu URL, active tab identity and tab links. It wraps the existing page template in one panel. Plugins with a single browser page retain their current menu entries. A plugin adding related pages can use this component with its own gated dispatcher and single workspace contribution.

Tabs use manual keyboard activation: arrows, Home and End move focus; Enter or Space activates a tab. Selection follows the server response. Concurrent tab requests supersede older requests, and accepted boundary replacements clean up the outgoing workspace before its DOM is removed. Unavailable pages preserve the current panel and existing request feedback. Viewers, maps and workers mount only in the new active panel.

The presentation uses the site's muted grey/white theme and adapts to narrow screens. Styles and keyboard handling are shared rather than repeated in each BIM page.
