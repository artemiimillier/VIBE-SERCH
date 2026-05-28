"""Vercel entry point - re-exports the FastAPI ASGI app."""

from src.web import app

__all__ = ["app"]
