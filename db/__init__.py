import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://edgeos:edgeos@localhost:5433/edgeos")
