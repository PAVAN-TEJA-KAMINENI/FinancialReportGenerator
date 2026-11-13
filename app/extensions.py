"""Flask extensions shared across the application.

Create extension objects here and bind them inside the application factory.
Importing this module must not create an application or touch the database.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
