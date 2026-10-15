"""Conservative, bounded activation policy; independent of deployment configuration."""
import re

from app.dsl import RuleError, TemplateBinding, template_parts
from app.paths import compile_path


def path_keys(path):
    return tuple(value for _, value in compile_path(path))


def finite_values(node):
    if node.get('op') == 'eq':
        return [node.get('value')]
    if node.get('op') == 'in' and node.get('value'):
        return node['value']
    return []


def guaranteed(node, predicate):
    """Positive proof only: all adds guarantees, every any branch must guarantee.

    Negation is intentionally never treated as evidence of a bounded scope.
    Predicate evaluation is relative to the current object's root.
    """
    if 'all' in node:
        return any(guaranteed(child, predicate) for child in node['all'])
    if 'any' in node:
        return all(guaranteed(child, predicate) for child in node['any'])
    if 'not' in node:
        return False
    return predicate(node)


def positive(node, paths, valid):
    values = finite_values(node)
    return bool(values) and 'path' in node and path_keys(node['path']) in paths and all(valid(v) for v in values)


def source_scope(node, source):
    if positive(node, {('issue', 'key')}, lambda v: isinstance(v, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,30}-[1-9][0-9]*', v)):
        return True
    if positive(node, {('issue', 'fields', 'project', 'key')}, lambda v: isinstance(v, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,30}', v)):
        return True
    if positive(node, {('issue', 'fields', 'project', 'id')}, lambda v: isinstance(v, str) and re.fullmatch(r'[0-9]+', v)):
        return True
    # Authenticated synthetic ingress also has to name a finite execution scope.
    return source == 'test' and positive(node, {('test_id',)}, lambda v: isinstance(v, str) and bool(v.strip()))


def change_scope(node):
    if 'some' not in node or path_keys(node['some']['path']) != ('changelog', 'items'):
        return False
    where = node['some']['where']
    field = lambda n: positive(n, {('field',), ('fieldId',)}, lambda v: isinstance(v, str) and bool(v.strip()))
    value = lambda n: positive(n, {('from',), ('to',), ('fromString',), ('toString',)},
                               lambda v: v is None or type(v) in {str, int, bool})
    # Both guarantees must refer to the SAME changed item, not two independent some clauses.
    finite_change = guaranteed(where, field) and guaranteed(where, value)
    # Assignment to ANY person is specific without hardcoding account IDs. Keep
    # every condition on the SAME changelog item and exclude missing/null/empty to.
    assignee = lambda n: positive(n, {('field',), ('fieldId',)}, lambda v: v == 'assignee')
    def to_check(n, op, expected):
        return ('path' in n and path_keys(n['path']) == ('to',) and n.get('op') == op
                and n.get('value', True) == expected and type(n.get('value', True)) is type(expected))
    assignment = (guaranteed(where, assignee)
                  and guaranteed(where, lambda n: to_check(n, 'exists', True))
                  and guaranteed(where, lambda n: to_check(n, 'ne', None))
                  and guaranteed(where, lambda n: to_check(n, 'ne', '')))
    return finite_change or assignment


def target_is_bounded(binding, index, actions):
    if isinstance(binding, TemplateBinding):
        parts = template_parts(binding.template)
        if len(parts) != 1 or parts[0][0] != 'path':
            return False
        path = parts[0][1]
    elif binding.path is None:
        return isinstance(binding.value, str) and bool(re.fullmatch(r'[A-Z][A-Z0-9_]{1,30}-[1-9][0-9]*', binding.value))
    else:
        if getattr(binding, 'default', None) is not None:
            return False
        path = binding.path
    keys = path_keys(path)
    if not keys:
        return False
    # Field references never iterate: the path grammar has no wildcards, slices,
    # queries or expressions. A parent/custom field is bounded just like issue.key.
    # Jira.issue_key checks the resolved STRING before any HTTP call or write budget
    # reservation; objects, lists, numeric IDs and JQL are not executable targets.
    if keys[0] != '_steps':
        return True
    return (len(keys) >= 3 and isinstance(keys[1], str)
            and keys[1].isdigit() and str(int(keys[1])) == keys[1] and int(keys[1]) < index
            and int(keys[1]) < len(actions))


def validate_safety(rule):
    """Called on validation, activation, event dispatch and before worker effects.

    Drafts remain editable; read-only rules do not require a write scope.
    This is not a proof that arbitrary external automations cannot form a loop.
    """
    if all(action.type == 'jira.issue.get' for action in rule.actions):
        return
    if not guaranteed(rule.when, lambda n: source_scope(n, rule.trigger.source)):
        raise RuleError('unsafe_write_scope: every matching branch must constrain issue.key or issue.fields.project.key/id with eq or nonempty in')
    if rule.trigger.event == 'jira:issue_updated' and not guaranteed(rule.when, change_scope):
        raise RuleError('unsafe_change_scope: issue_updated writes require some changelog.items with a positive field/fieldId and from/to/fromString/toString condition in the same where; assignee accepts to exists, ne null and ne empty string together')
    for index, action in enumerate(rule.actions):
        if action.type == 'jira.issue.transition' and (action.from_status_id is None or action.from_status_id == action.to_status_id):
            raise RuleError(f'unsafe_transition: action {index} requires distinct from_status_id and to_status_id')
        for name in ('issue', 'source', 'parent', 'inward', 'outward'):
            binding = getattr(action, name, None)
            if binding is not None and not target_is_bounded(binding, index, rule.actions):
                raise RuleError(f'unsafe_action_target: action {index} {name} must be an explicit issue key or a single field reference; future steps, partial templates and fallbacks are not allowed')
