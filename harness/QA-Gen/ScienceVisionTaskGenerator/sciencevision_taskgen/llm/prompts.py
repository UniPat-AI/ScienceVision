"""Prompt builders for builder augmentation and challenger semantic review.

Each function returns ``(system_prompt, user_prompt)`` as plain strings.
Both prompts are concise and ``response_format=json_object``-compatible.
"""
from __future__ import annotations

import json


def build_builder_messages(
    task: dict,
    asset: dict,
    template_instruction: str,
) -> tuple[str, str]:
    """Build system + user prompts for builder augmentation.

    The builder LLM rewrites a template instruction to reference a specific
    asset while preserving the template's scientific intent.

    Args:
        task: Task dict (must contain at least ``v_class`` and ``domain``).
        asset: Asset dict (must contain at least ``name`` and ``format``).
        template_instruction: The raw template instruction to be rewritten.

    Returns:
        ``(system_prompt, user_prompt)`` — both non-empty strings.
    """
    system = (
        "You rewrite a ScienceVision 3D-QA task instruction to reference a "
        "specific asset, keeping the scientific intent of the template "
        "unchanged.\n\n"
        "Rules:\n"
        "- The rewritten instruction must be answerable by inspecting the "
        "named asset.\n"
        "- Do not introduce facts that cannot be verified from the asset.\n"
        "- Use the asset's domain-appropriate terminology.\n"
        "- Preserve the original question type (measurement, comparison, "
        "identification, etc.).\n\n"
        "Return strict JSON only — no markdown fences, no extra keys.\n"
        "Schema: {\"instruction\": string, \"scientific_action\": string, "
        "\"asset_specific_rationale\": string}"
    )

    user_payload = {
        "domain": task.get("domain", ""),
        "v_class": task.get("v_class", ""),
        "asset_name": asset.get("name", ""),
        "asset_format": asset.get("format", ""),
        "template_instruction": template_instruction,
    }
    user = (
        "Rewrite the following template instruction for the given asset.\n\n"
        f"{json.dumps(user_payload, ensure_ascii=False)}\n\n"
        "Return a JSON object with keys: instruction, scientific_action, "
        "asset_specific_rationale."
    )
    return system, user


def build_challenger_messages(task: dict) -> tuple[str, str]:
    """Build system + user prompts for challenger semantic adversarial review.

    The challenger LLM audits a task spec for scientific invalidity,
    asset-question mismatch, shortcut risk, and terminology errors.

    Args:
        task: Task dict containing at minimum ``instruction``,
            ``scientific_action``, ``asset``, ``v_class``,
            ``terminal_answer``, and ``quality_gates``.

    Returns:
        ``(system_prompt, user_prompt)`` — both non-empty strings.
    """
    system = (
        "You audit a ScienceVision QA task spec for scientific quality.\n\n"
        "Check for these issue categories:\n"
        "- SCIENTIFIC_INVALID: claim or question that contradicts domain "
        "science.\n"
        "- ASSET_MISMATCH: question cannot be answered from the named asset.\n"
        "- SHORTCUT_RISK: question has a trivially guessable answer that "
        "does not require 3D reasoning.\n"
        "- TERMINOLOGY_ERROR: incorrect or ambiguous domain terminology.\n\n"
        "Return strict JSON only — an array of issue objects.\n"
        "Each issue schema: {\"code\": string, \"severity\": "
        "\"low\"|\"medium\"|\"high\"|\"critical\", \"detail\": string}\n"
        "Return an empty array [] if no issues are found."
    )

    task_payload = {
        k: task.get(k)
        for k in (
            "instruction",
            "scientific_action",
            "asset",
            "v_class",
            "terminal_answer",
            "quality_gates",
        )
    }
    user = (
        "Audit the following ScienceVision task spec and return a JSON array "
        "of issues (empty array if none).\n\n"
        f"{json.dumps(task_payload, ensure_ascii=False)}"
    )
    return system, user
