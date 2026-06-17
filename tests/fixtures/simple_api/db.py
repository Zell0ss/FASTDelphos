async def create_message(conn, content: str, author: str) -> None:
    await conn.execute(
        "INSERT INTO messages (content, author) VALUES (%s, %s)",
        (content, author),
    )


async def get_message(conn, msg_id: int) -> dict:
    return await conn.fetchone(
        "SELECT id, content, author FROM messages WHERE id = %s",
        (msg_id,),
    )
