"""Upload paths and validation owned by the BIM workspace's Django adapter."""
from django.conf import settings
from django.core.exceptions import ValidationError


def user_directory_path(instance, filename: str) -> str:
    # file will be uploaded to MEDIA_ROOT/user_<id>/<filename>
    return f"user_{instance.user.id}/{filename}"


# Default maximum upload sizes (in bytes). These can be overridden per
# deployment via the corresponding Django settings, but a sensible
# hardcoded default is used when the setting is not configured, so
# uploads are never left completely unbounded.
DEFAULT_MAX_CONFIG_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB - CSV/XLSX config files
DEFAULT_MAX_MODEL_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB - IFC building models


def validate_config_upload_size(value) -> None:
    """Reject config uploads (CSV/XLSX) larger than the configured maximum."""
    max_size = getattr(
        settings, "MODEL_MANAGER_MAX_CONFIG_UPLOAD_SIZE", DEFAULT_MAX_CONFIG_UPLOAD_SIZE
    )
    if value.size > max_size:
        raise ValidationError(
            f"File exceeds the maximum allowed size of {max_size} bytes."
        )


def validate_model_upload_size(value) -> None:
    """Reject IFC model uploads larger than the configured maximum."""
    max_size = getattr(
        settings, "MODEL_MANAGER_MAX_MODEL_UPLOAD_SIZE", DEFAULT_MAX_MODEL_UPLOAD_SIZE
    )
    if value.size > max_size:
        raise ValidationError(
            f"File exceeds the maximum allowed size of {max_size} bytes."
        )



def cityjson_source_path(instance, filename):
    from pathlib import Path
    from django.utils.text import get_valid_filename
    return f"cityjson/{instance.upload.user_id}/{instance.upload_id}/{get_valid_filename(Path(filename).name)}"
