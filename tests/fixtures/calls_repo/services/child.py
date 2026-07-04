from services.base import Greeter


class LoudGreeter(Greeter):
    def shout(self, name: str) -> str:
        return self.greet(name).upper()
