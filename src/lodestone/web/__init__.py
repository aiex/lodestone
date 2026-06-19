"""A localhost web dashboard over the registry (FastAPI).

Read-only. It never mutates the fleet — config.yaml stays the source of truth.
It renders agents/projects/permissions plus activity and AI usage/cost charts
straight from the SQLite registry.
"""
