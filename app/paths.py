"""Bounded v1 field references, never expressions or Python attribute access."""
from functools import lru_cache
import json
import re


MAX_PATH = 500
PATH_DESCRIPTION = (
    'Field reference relative to the object being read, without $. Use dots for '
    'ASCII identifiers, ["literal.key"] for exact JSON object keys (JSON string '
    'escapes supported), and [0] for zero-based array indexes. Indexes are '
    'nonnegative, without leading zeros. Inside some.where the root is the '
    'current item; inside jira.issue.get.require it is the fetched issue. '
    'Empty string selects the current object. No expressions, functions, '
    'wildcards, slices, JSON Pointer notation or recursion.'
)
PATH_EXAMPLES = ['issue.fields.summary', 'this["some.value"].is_literal',
                 'issue.fields.components[0].name', '["Nombre del repositorio"]',
                 '_steps["0"].key', '']
PATH_SYNTAX = {
    "syntax": "field-reference",
    "root": "implicit-current-object",
    "maxLength": MAX_PATH,
    "identifierPattern": "[A-Za-z_][A-Za-z0-9_]*",
    "literalKey": '["JSON string with escapes"]',
    "arrayIndex": "[0] (nonnegative integer, no leading zeros)",
    "examples": PATH_EXAMPLES,
}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\]")
_JSON = json.JSONDecoder()


class PathError(ValueError):
    pass


def check_path(path):
    # Validate before caching: untrusted nonstrings must not reach lru_cache.
    if not isinstance(path, str) or len(path) > MAX_PATH:
        raise PathError("Field paths must be strings of at most 500 characters")
    compile_path(path)
    return path


@lru_cache(maxsize=1024)
def compile_path(path):
    if not isinstance(path, str) or len(path) > MAX_PATH:
        raise PathError("Field paths must be strings of at most 500 characters")
    if path == "":
        return ()
    tokens, position = [], 0
    if not path.startswith("["):
        identifier = _IDENTIFIER.match(path, position)
        if identifier is None:
            raise PathError("Field paths start with a field or bracket selector, without $")
        tokens.append(("key", identifier.group()))
        position = identifier.end()
    while position < len(path):
        if path[position] == ".":
            identifier = _IDENTIFIER.match(path, position + 1)
            if identifier is None:
                raise PathError('After a dot use a field name; literal keys use ["key"]')
            tokens.append(("key", identifier.group()))
            position = identifier.end()
        elif path[position] == "[":
            start = position + 1
            if start < len(path) and path[start] == '"':
                try:
                    key, end = _JSON.raw_decode(path, start)
                    if end >= len(path) or path[end] != "]":
                        raise ValueError()
                    # Reject lone surrogate escapes; paired escapes decode to Unicode.
                    key.encode("utf-8")
                except (ValueError, UnicodeError):
                    raise PathError('Literal keys must be valid JSON strings inside ["key"]') from None
                tokens.append(("key", key))
                position = end + 1
            else:
                index = _INDEX.match(path, start)
                if index is None:
                    raise PathError('Brackets require ["key"] or a nonnegative index such as [0]')
                tokens.append(("index", int(index.group()[:-1])))
                position = index.end()
        else:
            raise PathError("Fields must be separated by a dot or bracket selector")
    return tuple(tokens)


def resolve(obj, path, missing):
    check_path(path)
    for kind, key in compile_path(path):
        if isinstance(obj, dict) and kind == "key":
            obj = obj.get(key, missing)
        elif isinstance(obj, list) and kind == "index":
            if key >= len(obj):
                return missing
            obj = obj[key]
        else:
            return missing
    return obj
