"""Application entrypoint.

Everything that used to live in this file now lives under app/. Gunicorn and the
Procfile still boot `server_pg:app`, and `server_pg.run_scheduled_analytics_command`
still resolves for the cron worker, so nothing about deployment changes.
"""

import os

from app import create_app
from app.worker import run_scheduled_analytics_command  # noqa: F401 - worker entrypoint

app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
