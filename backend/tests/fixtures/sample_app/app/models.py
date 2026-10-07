"""Domain models."""


class Base:
    def __init__(self) -> None:
        self.ready = True

    def save(self) -> None:
        """Persist the object."""


class User(Base):
    def __init__(self) -> None:
        super().__init__()

    def greet(self) -> str:
        self.save()
        return "hi"

