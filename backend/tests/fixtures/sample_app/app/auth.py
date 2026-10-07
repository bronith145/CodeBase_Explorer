import os

from . import utils
from .models import User


class JWTGenerator:
    def generate(self, user: User) -> str:
        return str(utils.helper())


def create_token(user: User) -> str:
    gen = JWTGenerator()
    return gen.generate(user)

