# Building selection previews

Model Manager, the building map, calculation and comparison choices show small previews from the actual model geometry. The public demo offers A–D house buttons with pre-generated images from its existing approved geometry assets. Text labels, native radio/checkbox controls and existing links remain usable while a preview loads or if it is unavailable.

Private previews use the owned BIM route `/plugins/bim/models/{upload}/thumbnail/`. A `building` query parameter selects one IFC building and its contained or decomposed products. Authentication, workflow enablement and upload ownership are checked before any source or image cache access. Private responses vary by cookie and prohibit HTTP storage; generated caches default to `data/building-thumbnail-cache`, outside static and uploaded-file roots. No model data is sent to a thumbnail service.

The server reuses the viewer's geometry cache, projects actual surfaces to a small lit orthographic PNG and preserves declared appearance. Space, opening and virtual volumes are hidden. Thumbnails are for recognizing models; they are not site plans, assessment results or photorealistic renders. The IFC, quantities, material records and viewer exports are unchanged.

Source content hashes, building GUIDs and a presentation version identify cached images. Source fingerprint changes invalidate the hash lookup. Atomic writes avoid partial images, invalid PNGs are regenerated, and cross-worker locks limit generation to two models at once. Selection pages supply URLs without generating geometry. The browser loads visible thumbnails through a bounded queue, shares repeated URLs within the page and cleans up pending work during HTMX navigation.

Authorization and upload resolution use Django's synchronous request lane. Geometry preparation and PNG rendering run in worker threads, so preparing a new preview does not hold that lane while other pages resolve their navigation and model metadata. Existing images return directly from the validated private cache.

Pillow is used for CPU image drawing and resizing, with no GPU, browser-rendering service or additional per-item WebGL context. Dependency versions are recorded in the lockfile. The public demo images do not expose private uploads or uploaded assessments.
