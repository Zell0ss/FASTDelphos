from fastapi import APIRouter, FastAPI

from .models import MessageIn, MessageOut

app = FastAPI()
router = APIRouter(prefix="/messages")


@router.post("/", response_model=MessageOut)
async def create_message(msg: MessageIn) -> MessageOut:
    return MessageOut(id=1, content=msg.content, author=msg.author)


@router.get("/{msg_id}", response_model=MessageOut)
async def get_message(msg_id: int) -> MessageOut:
    return MessageOut(id=msg_id, content="hello", author="alice")


app.include_router(router)
