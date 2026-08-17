from django.db import models
from djust.db import notify_on_save


@notify_on_save
class ReactionCount(models.Model):
    """Per-emoji running total for the Live Reactions hero demo."""

    emoji = models.CharField(max_length=8, unique=True)
    count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.emoji} ({self.count})"


@notify_on_save
class PollVote(models.Model):
    """Per-option running total for the Live Poll supporting demo."""

    option = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.option}: {self.count}"


@notify_on_save
class GuestbookMessage(models.Model):
    """One message in the Live Guestbook supporting demo."""

    text = models.CharField(max_length=280)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.text[:60]
