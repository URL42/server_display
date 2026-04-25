from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    todoist_api_token: str
    todoist_project_id: str
    db_path: str = "/data/bossbitch.db"

    class Config:
        env_file = ".env"

settings = Settings()
