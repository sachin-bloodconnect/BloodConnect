import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "bloodconnect-development-secret-key"
    )

    MYSQL_HOST = os.getenv(
        "MYSQL_HOST",
        "127.0.0.1"
    )

    MYSQL_USER = os.getenv(
        "MYSQL_USER",
        "root"
    )

    MYSQL_PASSWORD = os.getenv(
        "MYSQL_PASSWORD",
        ""
    )

    MYSQL_DATABASE = os.getenv(
        "MYSQL_DATABASE",
        "bloodconnect"
    )

    STOCK_CRITICAL = int(
        os.getenv("STOCK_CRITICAL", "2")
    )

    STOCK_LOW = int(
        os.getenv("STOCK_LOW", "10")
    )