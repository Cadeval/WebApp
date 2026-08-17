import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory


@pytest.mark.django_db
def test_theme_cookies_are_namespaced(client):
    """LIVEVIEW_CONFIG['theme']['cookie_namespace'] makes djust prefix its
    theme cookies (djstart_), so they don't collide with other djust apps on
    the same host. djust handles this natively — verify the prefix reaches the
    rendered anti-FOUC script."""
    response = client.get("/")
    content = response.content.decode()
    assert '__djust_theme_cookie_prefix = "djstart_"' in content


@pytest.mark.django_db
class TestMyceliumViewRebuildHelpers:
    """Test the data-shaping helpers directly, without going through @action."""

    def _mount(self):
        from apps.mycelium.views import MyceliumView

        rf = RequestFactory()
        req = rf.get("/")
        req.user = AnonymousUser()
        view = MyceliumView()
        view.mount(req)
        return view

    def test_mount_initializes_reactions_in_canonical_order(self):
        from apps.mycelium.views import EMOJIS

        view = self._mount()
        assert [r["emoji"] for r in view.reactions] == EMOJIS
        assert all(r["count"] == 0 for r in view.reactions)

    def test_mount_initializes_poll_in_canonical_order(self):
        from apps.mycelium.views import POLL_OPTIONS

        view = self._mount()
        assert [r["option"] for r in view.poll_data] == POLL_OPTIONS
        assert all(r["count"] == 0 for r in view.poll_data)
        assert all(r["pct"] == 0 for r in view.poll_data)

    def test_mount_initializes_messages_as_empty_list(self):
        view = self._mount()
        assert view.messages == []

    def test_rebuild_reactions_reflects_db_counts(self):
        from apps.mycelium.models import ReactionCount

        ReactionCount.objects.create(emoji="🔥", count=5)
        view = self._mount()
        fire = next(r for r in view.reactions if r["emoji"] == "🔥")
        assert fire["count"] == 5

    def test_rebuild_poll_computes_percentages(self):
        from apps.mycelium.models import PollVote

        PollVote.objects.create(option="Forms", count=3)
        PollVote.objects.create(option="Components", count=1)
        view = self._mount()
        forms = next(r for r in view.poll_data if r["option"] == "Forms")
        components = next(r for r in view.poll_data if r["option"] == "Components")
        realtime = next(r for r in view.poll_data if r["option"] == "Realtime")
        assert forms["count"] == 3
        assert forms["pct"] == 75.0
        assert components["pct"] == 25.0
        assert realtime["pct"] == 0

    def test_rebuild_messages_limits_to_five_newest_first(self):
        from apps.mycelium.models import GuestbookMessage

        for i in range(7):
            GuestbookMessage.objects.create(text=f"msg {i}")
        view = self._mount()
        assert len(view.messages) == 5
        assert view.messages[0]["text"] == "msg 6"
        assert view.messages[4]["text"] == "msg 2"

    def test_context_data_exposes_demo_state(self):
        view = self._mount()
        ctx = view.get_context_data()
        assert ctx["reactions"] == view.reactions
        assert ctx["poll_data"] == view.poll_data
        assert ctx["messages"] == view.messages


@pytest.mark.django_db
def test_home_page_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_reactioncount_starts_at_zero_when_created():
    from apps.mycelium.models import ReactionCount

    row, created = ReactionCount.objects.get_or_create(emoji="🔥")
    assert created is True
    assert row.count == 0


@pytest.mark.django_db
def test_pollvote_starts_at_zero_when_created():
    from apps.mycelium.models import PollVote

    row, created = PollVote.objects.get_or_create(option="Forms")
    assert created is True
    assert row.count == 0


@pytest.mark.django_db
def test_guestbookmessage_stores_text_and_timestamps():
    from apps.mycelium.models import GuestbookMessage

    msg = GuestbookMessage.objects.create(text="hello")
    assert msg.text == "hello"
    assert msg.created_at is not None


@pytest.mark.django_db
class TestMyceliumViewActions:
    def _mount(self):
        from apps.mycelium.views import MyceliumView

        rf = RequestFactory()
        req = rf.get("/")
        req.user = AnonymousUser()
        view = MyceliumView()
        view.mount(req)
        return view

    # ─── react ────────────────────────────────────────────────────────────

    def test_react_increments_db_count_and_state(self):
        from apps.mycelium.models import ReactionCount

        view = self._mount()
        view.react(emoji="🔥")
        assert ReactionCount.objects.get(emoji="🔥").count == 1
        fire = next(r for r in view.reactions if r["emoji"] == "🔥")
        assert fire["count"] == 1

    def test_react_is_cumulative(self):
        view = self._mount()
        view.react(emoji="❤️")
        view.react(emoji="❤️")
        view.react(emoji="❤️")
        heart = next(r for r in view.reactions if r["emoji"] == "❤️")
        assert heart["count"] == 3

    def test_react_ignores_invalid_emoji(self):
        from apps.mycelium.models import ReactionCount

        view = self._mount()
        view.react(emoji="💣")  # not in EMOJIS
        assert ReactionCount.objects.filter(emoji="💣").count() == 0

    # ─── vote ─────────────────────────────────────────────────────────────

    def test_vote_increments_db_count_and_state(self):
        from apps.mycelium.models import PollVote

        view = self._mount()
        view.vote(option="Forms")
        assert PollVote.objects.get(option="Forms").count == 1
        forms = next(r for r in view.poll_data if r["option"] == "Forms")
        assert forms["count"] == 1
        assert forms["pct"] == 100.0

    def test_vote_ignores_invalid_option(self):
        from apps.mycelium.models import PollVote

        view = self._mount()
        view.vote(option="Nonsense")
        assert PollVote.objects.filter(option="Nonsense").count() == 0

    def test_vote_recomputes_pct_across_options(self):
        view = self._mount()
        view.vote(option="Forms")
        view.vote(option="Forms")
        view.vote(option="Forms")
        view.vote(option="Auth")
        forms = next(r for r in view.poll_data if r["option"] == "Forms")
        auth = next(r for r in view.poll_data if r["option"] == "Auth")
        assert forms["pct"] == 75.0
        assert auth["pct"] == 25.0

    # ─── post_message ────────────────────────────────────────────────────

    def test_post_message_saves_and_appears_in_state(self):
        from apps.mycelium.models import GuestbookMessage

        view = self._mount()
        view.post_message(text="hello world")
        assert GuestbookMessage.objects.filter(text="hello world").exists()
        assert view.messages[0]["text"] == "hello world"

    def test_post_message_ignores_empty_text(self):
        from apps.mycelium.models import GuestbookMessage

        view = self._mount()
        view.post_message(text="")
        view.post_message(text="   ")
        view.post_message(text=None)
        assert GuestbookMessage.objects.count() == 0
        assert view.messages == []

    def test_post_message_trims_to_280_chars(self):
        from apps.mycelium.models import GuestbookMessage

        view = self._mount()
        view.post_message(text="x" * 500)
        saved = GuestbookMessage.objects.get()
        assert len(saved.text) == 280

    def test_post_message_keeps_only_five_newest_in_state(self):
        view = self._mount()
        for i in range(7):
            view.post_message(text=f"msg {i}")
        assert len(view.messages) == 5
        assert view.messages[0]["text"] == "msg 6"

    # ─── reset_demo ──────────────────────────────────────────────────────

    def test_reset_demo_clears_all_three_models(self):
        from apps.mycelium.models import GuestbookMessage, PollVote, ReactionCount

        view = self._mount()
        view.react(emoji="🔥")
        view.vote(option="Forms")
        view.post_message(text="hello")
        assert ReactionCount.objects.exists()
        assert PollVote.objects.exists()
        assert GuestbookMessage.objects.exists()

        view.reset_demo()

        assert not ReactionCount.objects.exists()
        assert not PollVote.objects.exists()
        assert not GuestbookMessage.objects.exists()
        assert all(r["count"] == 0 for r in view.reactions)
        assert all(r["count"] == 0 for r in view.poll_data)
        assert view.messages == []


@pytest.mark.django_db
def test_base_template_renders_sync_banner(client):
    response = client.get("/")
    content = response.content.decode()
    assert "Open this page in another window" in content


@pytest.mark.django_db
def test_base_template_renders_presence_chip(client):
    response = client.get("/")
    content = response.content.decode()
    assert "online" in content
    assert 'class="presence-chip"' in content


@pytest.mark.django_db
def test_home_page_renders_hero_reactions(client):
    response = client.get("/")
    content = response.content.decode()
    # Every emoji button is rendered
    for emoji in ["🔥", "❤️", "🚀", "👏", "💯", "😍"]:
        assert emoji in content
    assert 'class="reactions"' in content


@pytest.mark.django_db
def test_home_page_renders_poll(client):
    response = client.get("/")
    content = response.content.decode()
    for option in ["Forms", "Components", "Realtime", "Auth"]:
        assert option in content
    assert "Live poll" in content


@pytest.mark.django_db
def test_home_page_renders_guestbook(client):
    response = client.get("/")
    content = response.content.decode()
    assert "Live guestbook" in content
    assert "Say hi" in content


@pytest.mark.django_db
def test_home_page_includes_dj_view_binding(client):
    response = client.get("/")
    content = response.content.decode()
    assert 'dj-view="apps.mycelium.views.MyceliumView"' in content
