from pydantic import BaseModel


class MessageIn(BaseModel):
    content: str
    author: str


class MessageOut(BaseModel):
    id: int
    content: str
    author: str
