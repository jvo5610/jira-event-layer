"""Generic Jira Forms discovery and normalization without form-specific schemas."""
import json
import re
from typing import Any

from app.dsl import RuleError

ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,30}-[1-9][0-9]*$")
MAX_FORMS = 100
MAX_QUESTIONS = 2000
MAX_ENRICHMENT_BYTES = 5_000_000


def candidate(source: str, event_type: str, payload: dict[str, Any]) -> dict[str, str] | None:
    """Return bounded request metadata for JSM issue-created events.

    The request-type custom field ID is installation-specific, so detect its
    documented value shape instead of hard-coding customfield_10010.
    """
    if source != "jira" or event_type != "jira:issue_created" or not isinstance(payload, dict):
        return None
    issue = payload.get("issue")
    if not isinstance(issue, dict) or not isinstance(issue.get("fields"), dict):
        return None
    key = issue.get("key")
    if not isinstance(key, str) or not ISSUE_KEY.fullmatch(key):
        return None
    matches = []
    for value in issue["fields"].values():
        if not isinstance(value, dict) or not isinstance(value.get("requestType"), dict):
            continue
        request_type = value["requestType"]
        request_type_id = request_type.get("id")
        service_desk_id = request_type.get("serviceDeskId")
        if (isinstance(request_type_id, str) and request_type_id.isdigit()
                and isinstance(service_desk_id, str) and service_desk_id.isdigit()):
            matches.append({"issue_key": key, "request_type_id": request_type_id,
                            "service_desk_id": service_desk_id})
    return matches[0] if len(matches) == 1 else None


def _choice_value(question: dict[str, Any], answer: dict[str, Any]) -> Any:
    selected = answer.get("choices")
    choices = question.get("choices")
    if not isinstance(selected, list):
        return answer
    labels = {}
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict) and isinstance(choice.get("id"), (str, int)):
                label = choice.get("label")
                labels[str(choice["id"])] = label if isinstance(label, str) else choice["id"]
    values = [labels.get(str(identifier), identifier) for identifier in selected]
    return values[0] if question.get("type") == "cd" and len(values) == 1 else values


def answer_value(question: dict[str, Any], answer: Any) -> Any:
    """Produce a direct JSON value while retaining unrecognized structures."""
    if not isinstance(answer, dict):
        return answer
    if "choices" in answer:
        return _choice_value(question, answer)
    if "text" in answer:
        return answer["text"]
    if len(answer) == 1:
        return next(iter(answer.values()))
    return answer


def normalize(form: dict[str, Any]) -> dict[str, Any]:
    design = form.get("design")
    state = form.get("state")
    if not isinstance(design, dict) or not isinstance(state, dict):
        raise RuleError("jira_form_invalid")
    questions, answers = design.get("questions"), state.get("answers")
    if not isinstance(questions, dict) or not isinstance(answers, dict):
        raise RuleError("jira_form_invalid")
    if len(questions) > MAX_QUESTIONS or len(answers) > MAX_QUESTIONS:
        raise RuleError("jira_form_too_many_questions")

    normalized = {}
    warnings = []
    for question_id, answer in answers.items():
        question = questions.get(str(question_id))
        if not isinstance(question, dict):
            fallback = f"question_{question_id}"
            if fallback in normalized:
                raise RuleError("jira_form_duplicate_question_key")
            normalized[fallback] = answer
            warnings.append({"question_id": str(question_id), "reason": "definition_missing"})
            continue
        question_key = question.get("questionKey")
        if not isinstance(question_key, str) or not question_key or len(question_key) > 250:
            question_key = f"question_{question_id}"
            warnings.append({"question_id": str(question_id), "reason": "question_key_missing"})
        if question_key in normalized:
            fallback = f"question_{question_id}"
            if fallback in normalized:
                raise RuleError("jira_form_duplicate_question_key")
            warnings.append({"question_id": str(question_id), "reason": "question_key_duplicate",
                             "question_key": question_key})
            question_key = fallback
        normalized[question_key] = answer_value(question, answer)

    result = {"answers": normalized}
    if warnings:
        result["warnings"] = warnings
    try:
        size = len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode())
    except (TypeError, ValueError, OverflowError):
        raise RuleError("jira_form_invalid_json") from None
    if size > MAX_ENRICHMENT_BYTES:
        raise RuleError("jira_form_enrichment_too_large")
    return result


def build(index: list[dict[str, Any]], forms: list[dict[str, Any]]) -> dict[str, Any]:
    if len(index) != len(forms) or len(forms) > MAX_FORMS:
        raise RuleError("jira_form_index_invalid")
    enriched = []
    for metadata, form in zip(index, forms, strict=True):
        item = normalize(form)
        item["id"] = metadata["id"]
        for key in ("name", "submitted", "updated", "formTemplate"):
            if key in metadata:
                item[key] = metadata[key]
        enriched.append(item)
    result = {"forms": enriched}
    try:
        size = len(json.dumps(result, allow_nan=False, separators=(",", ":")).encode())
    except (TypeError, ValueError, OverflowError):
        raise RuleError("jira_form_invalid_json") from None
    if size > MAX_ENRICHMENT_BYTES:
        raise RuleError("jira_form_enrichment_too_large")
    return result
