"""Owned CityJSON exchange and building map pages in the BIM workflow."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.plugins.bim_model_manager.django.models import FileUpload, ModelConversion, BuildingLocation, CadevilDocument
from apps.plugins.bim_model_manager.building_locations import source_building_locations
from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash
from apps.plugins.bim_model_manager.cityjson_export import model_cityjson, CityJSONExportError, EXPORT_VERSION
from .exchange_forms import CityJSONImportForm, BuildingLocationForm
from .pages import bim_page, page


def cache_directory():
    return Path(getattr(settings, "CITYJSON_CACHE_ROOT", Path(settings.BASE_DIR) / "data" / "cityjson-cache"))


@contextmanager
def local_source(upload):
    """Support private Django storage without requiring a filesystem backend."""
    try:
        path = Path(upload.document.path)
    except (NotImplementedError, AttributeError):
        path = None
    if path is not None:
        yield path
    else:
        with tempfile.TemporaryDirectory(prefix="cadevil-model-source-") as folder:
            path = Path(folder) / "source.ifc"
            with upload.document.open("rb") as source, path.open("wb") as target:
                for chunk in source.chunks():
                    target.write(chunk)
            yield path


def _source_locations(upload):
    with local_source(upload) as source:
        return source_building_locations(source, cache_directory())


def _locations(upload, data):
    rows = [dict(row) for row in data["buildings"]]
    try:
        conversion = upload.conversion
    except ModelConversion.DoesNotExist:
        conversion = None
    derived = {}
    if conversion and conversion.output_sha256 == data["ifc_sha256"]:
        derived = {row["guid"]: row for row in conversion.metadata.get("buildings", []) if row.get("guid")}
    overrides = {row.guid: row for row in upload.building_locations.all()}
    for row in rows:
        original = derived.get(row["guid"])
        if original and original.get("latitude") is not None and original.get("longitude") is not None:
            row.update(latitude=original["latitude"], longitude=original["longitude"], source="cityjson_geometry",
                       status="located", message="Representative point of the original CityJSON building geometry.", crs=original.get("crs", ""))
        override = overrides.get(row["guid"])
        if override and override.source == "manual":
            row.update(latitude=float(override.latitude), longitude=float(override.longitude), source="manual", status="located",
                       message=override.note or "Owner-set map location; the IFC source coordinates are unchanged.", crs="EPSG:4326")
    return rows


@bim_page
@never_cache
@require_http_methods(["GET", "POST"])
def import_cityjson(request):
    from apps.plugins.bim_model_manager.cityjson_import import inspect_cityjson, convert_cityjson, CityJSONImportError
    form = CityJSONImportForm(request.POST if request.method == "POST" else None,
                              request.FILES if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        stored_files = []
        try:
            with tempfile.TemporaryDirectory(prefix="cadevil-cityjson-import-") as folder:
                source, output = Path(folder) / "input.city.json", Path(folder) / "output.ifc"
                supplied = form.cleaned_data["document"]
                with source.open("wb") as target:
                    for chunk in supplied.chunks():
                        target.write(chunk)
                inspection = inspect_cityjson(source)
                lod = form.cleaned_data["lod"] or inspection["default_lod"]
                metadata = convert_cityjson(source, output, lod=lod, name_attribute=form.cleaned_data["name_attribute"] or None)
                metadata.pop("output_path", None)
                metadata["source_version"] = inspection["version"]
                with transaction.atomic():
                    upload = FileUpload(user=request.user, description=form.cleaned_data["description"] or Path(supplied.name).stem[:255])
                    upload.document.save(Path(supplied.name).stem + ".ifc", ContentFile(output.read_bytes()), save=False)
                    stored_files.append(upload.document)
                    upload.save()
                    conversion = ModelConversion(upload=upload, source_sha256=metadata["source_sha256"],
                        output_sha256=metadata["output_sha256"], metadata=metadata)
                    conversion.source.save(Path(supplied.name).name, ContentFile(source.read_bytes()), save=False)
                    stored_files.append(conversion.source)
                    conversion.save()
        except (CityJSONImportError, OSError) as error:
            for stored in stored_files:
                stored.delete(save=False)
            form.add_error("document", str(error) if isinstance(error, CityJSONImportError) else "The model could not be stored. Please try again.")
        except Exception:
            for stored in stored_files:
                stored.delete(save=False)
            raise
        else:
            return redirect("bim:viewer", pk=upload.pk)
    return page(request, "bim/cityjson_import.html", {"title": "Import CityJSON", "form": form},
                status=400 if request.method == "POST" and form.errors else 200)


@bim_page
@never_cache
@require_http_methods(["GET"])
def download_cityjson_source(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    conversion = get_object_or_404(ModelConversion, upload=upload)
    return FileResponse(conversion.source.open("rb"), as_attachment=True, filename=Path(conversion.source.name).name)


def _cached_export(source):
    return cache_directory() / f"{EXPORT_VERSION}-{file_hash(source)}.city.json"


@bim_page
@never_cache
@require_http_methods(["GET", "POST"])
def export_cityjson(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    error = ""
    with local_source(upload) as source:
        if request.method == "POST":
            try:
                model_cityjson(source, cache_directory())
            except (CityJSONExportError, OSError) as failure:
                error = str(failure) if isinstance(failure, CityJSONExportError) else "The export could not be stored. Please try again."
        ready = _cached_export(source).exists()
    return page(request, "bim/cityjson_export.html", {"title": "Export CityJSON", "document": upload, "ready": ready, "error": error}, status=400 if error else 200)


@bim_page
@never_cache
@require_http_methods(["GET"])
def download_cityjson(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    with local_source(upload) as source:
        path = _cached_export(source)
    if not path.exists():
        return HttpResponse("Prepare this model's CityJSON export first.", status=409)
    return FileResponse(path.open("rb"), as_attachment=True, filename=Path(upload.document.name).stem + ".city.json", content_type="application/json")


@bim_page
@never_cache
@require_http_methods(["GET"])
def building_map(request):
    uploads = FileUpload.objects.filter(user=request.user).select_related("conversion").prefetch_related("building_locations").order_by("-uploaded_at")
    pagination = Paginator(uploads, 25).get_page(request.GET.get("page"))
    owned = list(pagination.object_list)
    rows = []
    # IFC parsing is independent per source; two bounded workers avoid a large
    # upload library multiplying memory usage or occupying all calculation CPUs.
    def read(upload):
        try:
            return _source_locations(upload), ""
        except (OSError, RuntimeError, ValueError):
            return None, "The IFC building locations could not be read. Open the model to inspect its source."
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(read, owned))
    reports = {}
    for document in CadevilDocument.objects.filter(user=request.user, upload__in=owned).only("pk", "upload_id").order_by("pk"):
        reports.setdefault(document.upload_id, []).append(document.pk)
    for upload, (data, error) in zip(owned, results):
        buildings = _locations(upload, data) if data else []
        if not buildings:
            buildings = [{"guid": "", "name": "No readable IFC building", "site_name": "", "latitude": None, "longitude": None,
                          "source": "", "status": "missing", "message": error or "This source contains no IfcBuilding. Its 3D viewer is still available.", "crs": ""}]
        for row in buildings:
            label = upload.description or Path(upload.document.name).name
            choices = reports.get(upload.pk, [])
            row.update(id=f"{upload.pk}:{row['guid']}", upload_id=str(upload.pk), title=f"{label} · {row['name']}",
                viewer_url=reverse("bim:viewer", args=[upload.pk]) + "?from=map",
                overview_url=reverse("material_passport:report", args=[choices[0]]) if len(choices) == 1 else reverse("bim:viewer", args=[upload.pk]) + "?from=map" if choices else "",
                location_url=reverse("bim:building_location", args=[upload.pk, row["guid"]]) if row["guid"] and row["status"] != "invalid" else "",
                context_url=reverse("bim:building_context", args=[upload.pk, row["guid"]]) if row["guid"] and row["status"] != "invalid" else "",
                thumbnail_url=reverse("bim:model_thumbnail", args=[upload.pk]) +
                    ("?" + urlencode({"building": row["guid"]}) if row["guid"] and row["status"] != "invalid" else ""))
            rows.append(row)
    return page(request, "bim/building_map.html", {"title": "Building map", "buildings": rows, "map_features_json": rows,
        "total_count": len(rows), "located_count": sum(row["status"] == "located" for row in rows), "pagination": pagination,
        "map_tile_url": getattr(settings, "BUILDING_MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")})


@bim_page
@never_cache
@require_http_methods(["GET", "POST"])
def building_location(request, pk, guid):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    data = _source_locations(upload)
    current = next((row for row in _locations(upload, data) if row["guid"] == guid and row["status"] != "invalid"), None)
    if current is None:
        raise Http404("This building is not in your uploaded model.")
    existing = BuildingLocation.objects.filter(upload=upload, guid=guid).first()
    form = BuildingLocationForm(request.POST if request.method == "POST" else None,
                                instance=existing, initial={"latitude": current["latitude"], "longitude": current["longitude"]})
    if request.method == "POST":
        if request.POST.get("action") == "reset":
            BuildingLocation.objects.filter(upload=upload, guid=guid, source="manual").delete()
            return redirect("bim:building_map")
        if form.is_valid():
            BuildingLocation.objects.update_or_create(upload=upload, guid=guid, defaults={
                "latitude": form.cleaned_data["latitude"], "longitude": form.cleaned_data["longitude"],
                "note": form.cleaned_data["note"], "source": "manual", "source_sha256": data["ifc_sha256"]})
            return redirect("bim:building_map")
    return page(request, "bim/building_location.html", {"title": "Building location", "document": upload,
        "building": current, "form": form, "has_override": bool(existing and existing.source == "manual")},
        status=400 if request.method == "POST" and form.errors else 200)
