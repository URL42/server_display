from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    todoist_api_token: str
    todoist_project_id: str
    db_path: str = "/data/server.db"

settings = Settings()
