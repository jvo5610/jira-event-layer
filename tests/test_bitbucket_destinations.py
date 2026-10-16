"""Provider credentials authorize rule destinations; no network or database needed."""
import httpx
import pytest

from app.config import Settings
from app.dsl import Action, RuleError
from app.worker import Bitbucket


@pytest.mark.parametrize("workspace,repository", [("team-one", "service-one"), ("team-two", "service-two")])
def test_connector_uses_rule_destination_without_server_repository_configuration(workspace, repository):
    settings = Settings({}, None, "", bitbucket_token="test-only")
    seen = []

    def provider(request):
        seen.append(request)
        return httpx.Response(201, json={"uuid": "test-pipeline"})

    action = Action(type="bitbucket.pipeline", workspace=workspace, repository=repository,
                    branch="main", pipeline="test-only")
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        response = Bitbucket(settings, client).call("POST", action, body={"target": {}})
    assert response.status_code == 201
    assert str(seen[0].url) == f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repository}/pipelines/"
    assert seen[0].headers["authorization"] == "Bearer test-only"


def test_connector_requires_provider_token():
    settings = Settings({}, None, "")
    action = Action(type="bitbucket.pipeline", workspace="team", repository="repo", pipeline="test-only")
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("must not call provider"))) as client:
        with pytest.raises(RuleError, match="connector_credentials_missing"):
            Bitbucket(settings, client).call("POST", action)


def test_provider_forbidden_response_is_not_bypassed():
    settings = Settings({}, None, "", bitbucket_token="test-only")
    action = Action(type="bitbucket.pipeline", workspace="team", repository="repo", pipeline="test-only")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as client:
        assert Bitbucket(settings, client).call("POST", action).status_code == 403
