import pytest
from fakes import FakeModel

from agent.config import get_settings
from agent.notify import RecordingNotifier
from agent.services import Services, set_services
from agent.store import InMemoryStore


class FakeServices(Services):
    paused = False

    def is_paused(self) -> bool:
        return self.paused

    def set_paused(self, paused: bool) -> None:
        self.paused = paused


@pytest.fixture
def services(monkeypatch, tmp_path):
    prefs = tmp_path / "preferences.yaml"
    prefs.write_text(
        """
jobs:
  sources: [{type: fake, boards: [acme]}]
  target_titles: [AI Engineer]
  skills: [python, langgraph, aws]
  locations: [remote]
  exclude_title_words: [intern]
  min_score: 65
  prefilter_min: 30
content:
  default_topic: shipping an agent with approvals
"""
    )
    monkeypatch.setenv("PREFERENCES_PATH", str(prefs))
    monkeypatch.setenv("APPROVAL_BASE_URL", "https://approve.example.com")
    monkeypatch.setenv("DRY_RUN", "true")
    get_settings.cache_clear()
    from agent.profile import load_preferences

    load_preferences.cache_clear()
    svc = FakeServices(
        settings=get_settings(),
        store=InMemoryStore(),
        notifier=RecordingNotifier(),
        drafter=FakeModel(),
        approval_secret="test-secret",
    )
    set_services(svc)
    yield svc
    set_services(None)
    get_settings.cache_clear()
    load_preferences.cache_clear()
