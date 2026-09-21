"""Turn a pattern's `variable` blocks into something callers can discover and check up front:
JSON Schema, allowed values lifted from `validation` blocks, an example request, and the
pattern repo's own `config.yaml` description.

Only rules in a recognised shape are lifted. Anything else is still enforced by Terraform at
plan time, exactly as before; lifting a rule only moves the same failure earlier.
"""

import json
import re
from typing import Any

import jsonschema

_TYPES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "list(string)": {"type": "array", "items": {"type": "string"}},
    "set(string)": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    "list(number)": {"type": "array", "items": {"type": "number"}},
    "map(string)": {"type": "object", "additionalProperties": {"type": "string"}},
}
_ABOUT_KEYS = ("description", "category", "components", "use_cases", "sizing", "estimated_costs")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')


def _mask_strings(text: str) -> str:
    """Same-length copy with string literal contents blanked, so operators can be found safely."""
    return _STRING.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', text)


def _split_and(condition: str) -> list[str]:
    """Top-level `&&` parts. A condition using `||` is not lifted at all."""
    masked = _mask_strings(condition)
    if "||" in masked or "?" in masked:
        return []
    parts, start = [], 0
    for match in re.finditer(r"&&", masked):
        parts.append(condition[start : match.start()])
        start = match.end()
    parts.append(condition[start:])
    return [p.strip() for p in parts]


def _bound(found: dict[str, Any], op: str, value: float, low: str, high: str, whole: bool):
    """Record `x <op> value`. For whole-number measures (length), `> 0` is the same as `>= 1`."""
    if whole and op == ">":
        op, value = ">=", value + 1
    if whole and op == "<":
        op, value = "<=", value - 1
    if op in (">=", "=="):
        found[low] = value
    if op in ("<=", "=="):
        found[high] = value
    if op == ">":
        found["exclusiveMinimum"] = value
    if op == "<":
        found["exclusiveMaximum"] = value


def lift(name: str, tf_type: str, condition: str) -> dict[str, Any]:
    """JSON Schema keywords equivalent to one Terraform validation condition (may be empty)."""
    var = re.escape(f"var.{name}")
    condition = condition.strip().removeprefix("${").removesuffix("}")
    found: dict[str, Any] = {}
    for part in _split_and(condition):
        if m := re.fullmatch(rf"contains\((\[.*\]),\s*{var}\)", part, re.S):
            try:
                found["enum"] = json.loads(m.group(1))
            except ValueError:
                continue
        elif m := re.fullmatch(rf'can\(regex\("(.*)",\s*{var}\)\)', part, re.S):
            pattern = m.group(1).replace("\\\\", "\\")
            try:
                re.compile(pattern)
            except re.error:
                continue
            found["pattern"] = pattern
        elif m := re.fullmatch(rf"{var}\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)", part):
            _bound(found, m.group(1), float(m.group(2)), "minimum", "maximum", False)
        elif m := re.fullmatch(rf"length\({var}\)\s*(>=|<=|>|<|==)\s*(\d+)", part):
            kind = "Length" if tf_type == "string" else "Items"
            _bound(found, m.group(1), int(m.group(2)), f"min{kind}", f"max{kind}", True)
    return found


def _tidy(number: float) -> int | float:
    return int(number) if float(number).is_integer() else number


def describe(variable: dict[str, Any]) -> dict[str, Any]:
    """One input as shown to callers: the raw variable plus any lifted rules and their messages."""
    rules: dict[str, Any] = {}
    for validation in variable["validations"]:
        rules |= lift(variable["name"], variable["type"], validation["condition"])
    shown = {k: v for k, v in variable.items() if k != "validations"}
    if "enum" in rules:
        shown["allowed_values"] = rules["enum"]
    for key in (
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    ):
        if key in rules:
            shown[key] = _tidy(rules[key]) if isinstance(rules[key], float) else rules[key]
    shown["rules"] = [v["error_message"] for v in variable["validations"]]
    return shown


def json_schema(name: str, variables: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for variable in variables:
        prop = dict(_TYPES.get(variable["type"], {}))
        if variable["description"]:
            prop["description"] = variable["description"]
        if not variable["required"] and variable["default"] is not None:
            prop["default"] = variable["default"]
        for validation in variable["validations"]:
            prop |= lift(variable["name"], variable["type"], validation["condition"])
        properties[variable["name"]] = prop
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{name} inputs",
        "type": "object",
        "properties": properties,
        "required": [v["name"] for v in variables if v["required"]],
        "additionalProperties": False,
    }


def errors(name: str, variables: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict]:
    """Validation problems, using the pattern author's own error message where a rule was lifted."""
    messages = {v["name"]: [x["error_message"] for x in v["validations"]] for v in variables}
    validator = jsonschema.Draft202012Validator(json_schema(name, variables))
    problems = []
    for error in sorted(validator.iter_errors(inputs), key=lambda e: list(e.path)):
        field = str(error.path[0]) if error.path else None
        authored = messages.get(field, []) if error.validator not in ("type", "required") else []
        # Never echo the submitted value back; describe the rule instead.
        if authored:
            message = " ".join(authored)
        elif error.validator == "required":
            message = error.message  # "'x' is a required property"
        elif error.validator == "additionalProperties":
            message = "unknown input; see GET /patterns/{name} for the accepted inputs"
        else:
            message = f"must satisfy {error.validator}: {json.dumps(error.validator_value)}"
        problems.append({"field": field, "message": message})
    return problems


def example(name: str, version: str | None, variables: list[dict[str, Any]]) -> dict[str, Any]:
    """A ready-to-edit request body: required inputs only, using allowed values where known."""
    inputs: dict[str, Any] = {}
    for variable in (v for v in variables if v["required"]):
        shown = describe(variable)
        placeholder = f"<{variable['name']}>"
        if "allowed_values" in shown:
            inputs[variable["name"]] = shown["allowed_values"][0]
        elif variable["type"] == "number":
            inputs[variable["name"]] = shown.get("minimum", 0)
        elif variable["type"] == "bool":
            inputs[variable["name"]] = False
        elif variable["type"].startswith(("list(", "set(")):
            inputs[variable["name"]] = [placeholder]
        elif variable["type"].startswith("map("):
            inputs[variable["name"]] = {}
        else:
            inputs[variable["name"]] = placeholder
    body: dict[str, Any] = {"pattern": name, "inputs": inputs}
    if version:
        body["version"] = version
    return body


def about(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The catalog-facing part of a pattern repo's `config.yaml`."""
    raw = raw if isinstance(raw, dict) else {}
    found = {key: raw[key] for key in _ABOUT_KEYS if key in raw}
    if isinstance(found.get("description"), str):
        found["description"] = " ".join(found["description"].split())
    return found
