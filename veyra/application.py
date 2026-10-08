"""Veyra Application - Entry point for the Veyra image viewer."""

from __future__ import annotations

import os
from pathlib import Path

from betrayer.application import BetrayerApplication
from betrayer.web.adapter import FlaskAdapter
from betrayer.web.routing import WebRouter
from betrayer.web.response import Response

from veyra.services.database import install_database_for_app
from veyra.web.routes import create_flask_app


def create_app() -> BetrayerApplication:
    """Create and configure the Veyra Betrayer application.
    
    This function follows the Betrayer framework pattern:
    1. Creates a BetrayerApplication instance (composition root).
    2. Bootstraps the application lifecycle.
    3. Installs the Betrayer Data Layer database (``database`` service).
    4. Creates the Flask adapter with web routes.
    5. Stores the Flask app for runtime access.
    
    Returns:
        Configured BetrayerApplication with Flask web layer.
    """
    # Create Betrayer application instance
    app = BetrayerApplication(name="veyra")
    
    # Bootstrap the Betrayer application lifecycle
    app.bootstrap().initialize().start()

    # Install the connected Betrayer database (SQLite as storage engine behind
    # the Betrayer DatabaseManager contract) and create the Veyra schema.
    install_database_for_app(app)

    # Create Flask adapter with routes and build the Flask app
    adapter = create_flask_app(app)
    
    # Store the Flask app on the Betrayer app for WSGI access
    app._flask_app = adapter.flask_app
    
    return app


def run_app(host: str = "127.0.0.1", port: int = 8349, debug: bool = False) -> None:
    """Run the Veyra application on the Flask development server.
    
    Args:
        host: The host address to bind to.
        port: The port to listen on.
        debug: Whether to enable Flask debug mode.
    """
    app = create_app()
    
    # Run the Flask development server
    flask_app = app._flask_app
    flask_app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_app()