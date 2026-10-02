from django_bolt import (
    BoltAPI,
    ReadOnlyModelViewSet,
)

from apps.plugin_manager.models import PluginRecord
from apps.plugin_manager.serializers import PluginRecordSerializer


class PluginRecordViewSet(ReadOnlyModelViewSet):
    """List/retrieve discovered plugins, and enable/disable them.

    Restricted to admin users. Enabling/disabling a plugin changes which of
    its contributed extensions ``PluginRegistry.get_active`` returns. The
    collection reload action discovers package changes without a restart.
    """

    queryset = PluginRecord.objects.all()
    serializer_class = PluginRecordSerializer
    permission_classes = []
    lookup_field = "plugin_id"
    lookup_value_regex = "[^/]+"
    api = BoltAPI()
