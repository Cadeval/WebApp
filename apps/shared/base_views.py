from djust import LiveView
from djust.presence import PresenceMixin


class BaseLiveView(PresenceMixin, LiveView):
    """Project-wide LiveView base.

    djust ≥ 1.0.0rc12 makes presence zero-config:
      - track_presence() no-ops during HTTP mount (no orphan entries)
      - track/untrack auto-broadcast so peer sessions refresh their chip
      - self.online_count is auto-set; templates use {{ online_count|default:1 }}
      - presence_unique_per_connection=True gives anonymous tabs distinct user_ids
      - abstract = True opts this base out of V001/V005 mount-readiness checks
    """

    abstract = True
    login_required = False  # acknowledge public access (silences djust.S005)
    presence_unique_per_connection = True

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        # No-op in HTTP mount; in WS mount: registers, sets self.online_count,
        # and broadcasts to peer sessions.
        self.track_presence()
