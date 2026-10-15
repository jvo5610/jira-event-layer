"""Dedicated webhook receiver. No management routes or administrator bootstrap."""
from app.main import create_app

app = create_app(webhooks=True)
