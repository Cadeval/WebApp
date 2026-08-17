import inspect

from djust import action
from djust.push import push_to_view

from apps.shared.base_views import BaseLiveView

from .models import GuestbookMessage, PollVote, ReactionCount

EMOJIS = ["🔥", "❤️", "🚀", "👏", "💯", "😍"]
POLL_OPTIONS = ["Forms", "Components", "Realtime", "Auth"]


class MyceliumView(BaseLiveView):
    template_name = "mycelium/mycelium.html"
    presence_key = "mycelium:landing"  # all sessions of MyceliumView share this bucket

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.emojis = EMOJIS
        self.poll_options = POLL_OPTIONS
        self._rebuild_reactions()
        self._rebuild_poll()
        self._rebuild_messages()
        # Source code shown in the demo panels — pulled live from this file
        # so editing the handler updates the displayed code automatically.
        self.react_src = inspect.getsource(MyceliumView.react)
        self.vote_src = inspect.getsource(MyceliumView.vote)
        self.post_message_src = inspect.getsource(MyceliumView.post_message)

    # ─── state helpers ────────────────────────────────────────────────────

    def _rebuild_reactions(self):
        counts = dict(ReactionCount.objects.values_list("emoji", "count"))
        self.reactions = [{"emoji": e, "count": counts.get(e, 0)} for e in EMOJIS]

    def _rebuild_poll(self):
        counts = dict(PollVote.objects.values_list("option", "count"))
        total = sum(counts.values())
        self.poll_data = [
            {
                "option": opt,
                "count": counts.get(opt, 0),
                "pct": (counts.get(opt, 0) / total * 100) if total > 0 else 0,
            }
            for opt in POLL_OPTIONS
        ]

    def _rebuild_messages(self):
        rows = GuestbookMessage.objects.order_by("-created_at")[:5].values(
            "text", "created_at"
        )
        self.messages = [
            {"text": r["text"], "created_at": r["created_at"].isoformat()} for r in rows
        ]

    # ─── @action handlers ────────────────────────────────────────────────

    @action
    def react(self, emoji="", **kwargs):
        if emoji not in EMOJIS:
            return
        row, _ = ReactionCount.objects.get_or_create(emoji=emoji)
        row.count += 1
        row.save()
        self._rebuild_reactions()
        push_to_view(
            "apps.mycelium.views.MyceliumView",
            state={"reactions": self.reactions},
        )

    @action
    def vote(self, option="", **kwargs):
        if option not in POLL_OPTIONS:
            return
        row, _ = PollVote.objects.get_or_create(option=option)
        row.count += 1
        row.save()
        self._rebuild_poll()
        push_to_view(
            "apps.mycelium.views.MyceliumView",
            state={"poll_data": self.poll_data},
        )

    @action
    def post_message(self, text="", **kwargs):
        text = (text or "").strip()[:280]
        if not text:
            return
        GuestbookMessage.objects.create(text=text)
        self._rebuild_messages()
        push_to_view(
            "apps.mycelium.views.MyceliumView",
            state={"messages": self.messages},
        )

    @action
    def reset_demo(self, **kwargs):
        """Wipe all three demos back to empty. No auth — demo only."""
        ReactionCount.objects.all().delete()
        PollVote.objects.all().delete()
        GuestbookMessage.objects.all().delete()
        self._rebuild_reactions()
        self._rebuild_poll()
        self._rebuild_messages()
        push_to_view(
            "apps.mycelium.views.MyceliumView",
            state={
                "reactions": self.reactions,
                "poll_data": self.poll_data,
                "messages": self.messages,
            },
        )
