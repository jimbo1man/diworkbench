
from __future__ import annotations

from typing import Any, Dict, List


REQUIRED_CONTRACT_FIELDS = [
    "objective",
    "requirements",
    "constraints",
    "architecture",
]


def validate_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(contract, dict):
        return {
            "status": "fail",
            "missing": REQUIRED_CONTRACT_FIELDS,
            "errors": ["Contract must be a JSON object / Python dict."],
        }

    missing = [field for field in REQUIRED_CONTRACT_FIELDS if field not in contract]
    errors: List[str] = []

    if "requirements" in contract and not isinstance(contract["requirements"], list):
        errors.append("'requirements' must be a list.")

    if "constraints" in contract and not isinstance(contract["constraints"], list):
        errors.append("'constraints' must be a list.")

    if "objective" in contract and not str(contract["objective"]).strip():
        errors.append("'objective' cannot be empty.")

    if "architecture" in contract and not isinstance(contract["architecture"], (dict, list, str)):
        errors.append("'architecture' must be a dict, list, or string.")

    status = "pass" if not missing and not errors else "fail"

    return {
        "status": status,
        "missing": missing,
        "errors": errors,
    }
