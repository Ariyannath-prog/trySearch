"""Vercel function entrypoint for the Flask application."""

from server_pg import app

__all__ = ["app"]