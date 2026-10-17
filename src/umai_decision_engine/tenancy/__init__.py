"""Admission before the pipeline (ADR-0023): credential → tenant, scopes, limits.

Receives headers and sizes, never content. Imports ``contracts``, ``policy`` (signature
verification) and ``settings`` only (code-layout boundaries).
"""
