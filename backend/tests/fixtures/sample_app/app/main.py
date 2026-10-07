from fastapi import Depends, FastAPI

from app.auth import create_token
from app.models import User

app = FastAPI()


def get_user() -> User:
    return User()


@app.get("/token")
def token(u: User = Depends(get_user)) -> str:
    return create_token(u)

