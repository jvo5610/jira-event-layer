"""Executable documentation checks; no provider calls or operator credentials."""
import io
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

from app.dsl import parse_rule
from app.safety import validate_safety
from test_integration import env


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def headings(text):
    # GitHub keeps Unicode in heading IDs, removes punctuation, and replaces spaces.
    outside = re.sub(r"```.*?```", "", text, flags=re.S)
    return {re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
            for title in re.findall(r"^#{1,6} (.+)$", outside, re.M)}


def test_documentation_local_links_and_anchors_exist():
    for document in DOCUMENTS:
        for target in re.findall(r"\]\(([^\s)]+)\)", document.read_text()):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            destination = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            assert destination.exists(), (document.name, target)
            if parsed.fragment and destination.suffix == ".md":
                assert unquote(parsed.fragment) in headings(destination.read_text()), (document.name, target)


def test_readme_complete_yaml_examples_are_valid_and_safe():
    blocks = re.findall(r"```yaml\n(.*?)```", (ROOT / "README.md").read_text(), re.S)
    complete = [block for block in blocks if block.startswith("apiVersion:")]
    assert complete
    for block in complete:
        rule, _ = parse_rule(block)
        validate_safety(rule)


def test_documented_http_walkthrough_against_real_api(env, monkeypatch, tmp_path, capsys):
    client, store, settings = env
    token_file = tmp_path / "delegated-token"
    token_file.write_text(settings.admin_bootstrap_token)
    monkeypatch.setenv("RELAY_API_URL", "http://127.0.0.1:8088")
    monkeypatch.setenv("RELAY_TOKEN_FILE", str(token_file))
    calls = []

    class InProcessAPI:
        def open(self, request, timeout):
            path = urlsplit(request.full_url).path
            calls.append(path)
            response = client.request(request.get_method(), path,
                                      content=request.data, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "failure", {}, None)
            return io.BytesIO(response.content)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: InProcessAPI())
    text = (ROOT / "docs/technical-reference.md").read_text()
    script, = re.findall(r"```python\n(.*?)```", text, re.S)
    exec(compile(script, "docs/technical-reference.md", "exec"), {})
    assert calls == ["/v1/rules/validate", "/v1/evaluate", "/v1/rules"]
    assert store.query("SELECT active_version FROM rules WHERE name='nuevo-flujo'") == [{"active_version": None}]
    assert store.query("SELECT * FROM runs") == []
    assert settings.admin_bootstrap_token not in capsys.readouterr().out
