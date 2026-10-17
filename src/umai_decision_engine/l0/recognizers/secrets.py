"""Secrets and credentials (docs/architecture/l0-entity-catalog.md, "beyond PII").

Provider-prefixed keys are specific enough on their own; generic secrets (passwords) are only
reported in an assignment or with an explicit word next to them. Spans cover the secret
value, not the surrounding words, so masking keeps the sentence readable.
"""

from __future__ import annotations

import base64
import binascii
import json

import regex

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec

_TRAILING = ".,;:)]}>'\"’”"


def _strip(value: str) -> str:
    return value.strip()


def _structural(fragment: str) -> list[Candidate]:
    """The pattern itself proves the structure (provider prefix, PEM armour, URI with
    credentials): validated, so an IP or e-mail inside it does not win the overlap."""

    value = fragment.rstrip(_TRAILING)
    return [Candidate(0, len(value), True)] if value else []


def _refine_jwt(fragment: str) -> list[Candidate]:
    header = fragment.split(".", 1)[0]
    try:
        decoded = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(decoded, dict) or "alg" not in decoded:
        return []
    return [Candidate(0, len(fragment), True)]


API_KEY = RecognizerSpec(
    entity="API_KEY",
    patterns=(
        PatternSpec(
            "aws_access_key_id",
            r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])",
            0.85,
        ),
        PatternSpec(
            "github_token", r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9_])", 0.9
        ),
        PatternSpec(
            "github_pat", r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{22,255}(?![A-Za-z0-9_])", 0.9
        ),
        PatternSpec(
            "gitlab_token", r"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])", 0.9
        ),
        PatternSpec(
            "slack_token", r"(?<![A-Za-z0-9_])xox[abposr]-[A-Za-z0-9\-]{10,}(?![A-Za-z0-9\-])", 0.85
        ),
        PatternSpec(
            "stripe_secret",
            r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}(?![A-Za-z0-9])",
            0.85,
        ),
        PatternSpec(
            "anthropic_key", r"(?<![A-Za-z0-9_\-])sk-ant-[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])", 0.9
        ),
        PatternSpec(
            "openai_key",
            r"(?<![A-Za-z0-9_\-])sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9_\-])",
            0.8,
        ),
        PatternSpec(
            "google_api_key", r"(?<![A-Za-z0-9_\-])AIza[0-9A-Za-z_\-]{35}(?![A-Za-z0-9_\-])", 0.85
        ),
        PatternSpec(
            "sendgrid_key",
            r"(?<![A-Za-z0-9_])SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])",
            0.9,
        ),
    ),
    canonical=_strip,
    refine=_structural,
    priority=6,
)

# AWS secret access keys have no prefix: 40 base64 characters are common, so the generic
# shape needs "secret" context next to it.
_AWS_SECRET_ACCESS_KEY = RecognizerSpec(
    entity="API_KEY",
    name="umai_api_key_aws_secret",
    patterns=(
        PatternSpec(
            "aws_secret_access_key",
            r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])",
            0.1,
            requires_context=True,
        ),
    ),
    canonical=_strip,
    context=("aws_secret_access_key", "secret access key", "secret_key", "secret key", "secret"),
    context_boost=0.5,
    priority=6,
)

PRIVATE_KEY = RecognizerSpec(
    entity="PRIVATE_KEY",
    patterns=(
        PatternSpec(
            "pem_private_key",
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]{0,8192}?"
            r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)",
            0.95,
        ),
    ),
    canonical=_strip,
    refine=_structural,
    priority=8,
)

JWT = RecognizerSpec(
    entity="JWT",
    patterns=(
        PatternSpec(
            "jwt",
            r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?![A-Za-z0-9_\-])",
            0.85,
        ),
    ),
    canonical=_strip,
    refine=_refine_jwt,
    priority=6,
)

CONNECTION_STRING = RecognizerSpec(
    entity="CONNECTION_STRING",
    patterns=(
        PatternSpec(
            "uri_with_credentials",
            r"(?i)(?<![\w])[a-z][a-z0-9+.\-]{1,20}://[^\s:/@]{1,128}:[^\s@/]{1,256}@[^\s/\"'<>]{1,255}[^\s\"'<>]*",
            0.85,
        ),
        PatternSpec(
            "kv_connection_string",
            r"(?i)(?:server|host|data source)\s*=[^;\n]{1,255};[^\n]{0,512}?"
            r"(?:password|pwd)\s*=[^;\n]{1,256};?",
            0.85,
        ),
    ),
    canonical=_strip,
    refine=_structural,
    priority=7,
)

# Password assignments: report the value only.
_PASSWORD_VALUE = regex.compile(r"(?:[:=]|\bis\b|\bolarak\b)\s*[\"']?(?P<v>[^\s\"',;]{4,128})")
# Turkish word order: "şifresi <value> olarak değişti"
_PASSWORD_VALUE_BEFORE = regex.compile(r"\s(?P<v>[^\s\"',;]{4,128})\s+olarak\b")


def _refine_password(fragment: str) -> list[Candidate]:
    match = _PASSWORD_VALUE.search(fragment) or _PASSWORD_VALUE_BEFORE.search(fragment)
    if not match:
        return []
    return [Candidate(match.start("v"), match.end("v"), None)]


PASSWORD = RecognizerSpec(
    entity="PASSWORD",
    patterns=(
        PatternSpec(
            "password_assignment",
            r"(?i)(?<![A-Za-z0-9])(?:password|passwd|pass(?:word)?_?|pwd|parola(?:m|sı|nız)?|şifre(?:m|si|niz)?|sifre(?:m|si)?)"
            r"\s*(?:[:=]|\bis\b|\bolarak\b)\s*[\"']?[^\s\"',;]{4,128}",
            0.7,
        ),
        PatternSpec(
            "password_tr_value_first",
            r"(?i)(?<![A-Za-z0-9])(?:şifre(?:si|m|niz)|parola(?:sı|m|nız))\s+[^\s\"',;]{4,128}"
            r"\s+olarak\b",
            0.7,
        ),
    ),
    canonical=_strip,
    refine=_refine_password,
    priority=5,
)

SECRET_SPECS: tuple[RecognizerSpec, ...] = (
    API_KEY,
    _AWS_SECRET_ACCESS_KEY,
    PRIVATE_KEY,
    JWT,
    CONNECTION_STRING,
    PASSWORD,
)
