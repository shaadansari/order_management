"""Pydantic schemas — what the API accepts (request) and returns (response).

Kept separate from DB models on purpose: what we STORE (hashed password) and what we
EXPOSE (never the password) are different concerns.
"""
