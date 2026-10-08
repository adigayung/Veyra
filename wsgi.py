"""Veyra WSGI entry point for production servers (nginx, gunicorn, etc)."""

from __future__ import annotations

from veyra.application import create_app

# Create the Betrayer application with Flask adapter
app = create_app()

# Expose the Flask app as the WSGI application
application = app._flask_app


if __name__ == "__main__":
    # When run directly, start the development server
    app._flask_app.run(host="127.0.0.1", port=8349, debug=True)