from django.apps import AppConfig
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models.query import QuerySet


class ModelEvaluatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.model_manager"

    def ready(self) -> None:

        def _get_group_permissions(user_obj) -> QuerySet[Permission, Permission]:
            group_model = settings.AUTH_GROUP_MODEL
            related_name_to_group = group_model.split(".")[-1].lower()

            user_groups_field = get_user_model().get_deferred_fields(
                f"{related_name_to_group}s"
            )
            user_groups_query = (
                f"{related_name_to_group}__{user_groups_field.related_query_name()}"
            )
            return Permission.objects.filter(**{user_groups_query: user_obj})
