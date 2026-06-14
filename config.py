# config.py

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    username = os.getenv("username")
    userpath = os.getenv("userpath")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    @classmethod
    def validate(cls):
        required = [
            "username",
            "userpath",
        ]
        missing = [k for k in required if not getattr(cls, k)]
        if missing:
            raise ValueError(f"Missing required config values: {', '.join(missing)}")

    @classmethod
    def validate_openai(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError("Missing required config value: OPENAI_API_KEY")


if __name__ == "__main__":
    try:
        Config.validate()
        print("All required configuration values are set.")
    except ValueError as e:
        print(e)

    try:
        Config.validate_openai()
        print("OpenAI configuration value is set.")
    except ValueError as e:
        print(e)

    print(Config().userpath)
