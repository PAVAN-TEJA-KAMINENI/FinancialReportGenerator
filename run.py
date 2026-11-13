"""Development server entry point.

Start the application from the project root:

    .venv\\Scripts\\python run.py
"""

import os

from app import create_app

app = create_app(os.environ.get("FLASK_CONFIG", "development"))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
