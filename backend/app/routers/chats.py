"""Chat between clients and masters.

Clients have no account: their browser keeps a random chat key (X-Client-Key header) and the server stores
only its SHA-256 — the same idea as the private request link. Masters use their normal login.
A client can write to any master from the master's profile, before or after leaving a request.
"""
import hashlib

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user_optional
from app.config import settings
from app.database import get_db
from app.models import ChatMessage, Conversation, Master, User, UserRole, UserStatus, utcnow
from app.notify import notify, notify_client
from app.rate_limit import rate_limit
from app.schemas import ChatMessageIn, ChatMessageOut, ChatStartIn, ConversationOut, MasterBriefOut, UnreadCountOut
from app.uploads import save_upload

router = APIRouter(prefix="/chats", tags=["chats"])

start_rate_limit = rate_limit("chat-start", max_attempts=20, window_seconds=3600)
message_rate_limit = rate_limit("chat-message", max_attempts=60, window_seconds=600)
MAX_MESSAGES_PAGE = 200


class Party:
    """Who is calling: a master (by login) or a client (by chat key)."""

    def __init__(self, master: Master | None = None, key_hash: str | None = None):
        self.master = master
        self.key_hash = key_hash

    @property
    def side(self) -> str:
        return "master" if self.master else "client"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _valid_key(key: str | None) -> str | None:
    if key and 32 <= len(key) <= 128 and all(c.isalnum() or c in "-_" for c in key):
        return key
    return None


def get_party(
    current_user: User | None = Depends(get_current_user_optional),
    x_client_key: str | None = Header(None),
) -> Party:
    if current_user is not None and current_user.role == UserRole.master and current_user.master:
        return Party(master=current_user.master)
    key = _valid_key(x_client_key)
    if key:
        return Party(key_hash=_hash(key))
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен ключ чата")


def _client_party(x_client_key: str | None = Header(None)) -> Party:
    key = _valid_key(x_client_key)
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен ключ чата")
    return Party(key_hash=_hash(key))


def _get_conversation(db: Session, conversation_id: int, party: Party) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    allowed = conversation is not None and (
        (party.master is not None and conversation.master_id == party.master.id)
        or (party.key_hash is not None and conversation.client_key_hash == party.key_hash)
    )
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Чат не найден")
    return conversation


def _brief(master: Master) -> MasterBriefOut:
    return MasterBriefOut(
        id=master.id, name=master.user.name, rating=master.rating, verified=master.verified,
        completed_orders=master.completed_orders, phone=master.user.phone, photo=master.user.photo,
    )


def _out(conversation: Conversation, party: Party, last: ChatMessage | None) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        master=_brief(conversation.master),
        client_name=conversation.client_name,
        last_message=last.text if last else None,
        last_is_photo=bool(last and last.photo),
        last_sender=last.sender if last else None,
        last_message_at=conversation.last_message_at,
        unread=conversation.master_unread if party.master else conversation.client_unread,
        blocked=conversation.blocked_by_master,
    )


def _add_message(db: Session, conversation: Conversation, sender: str, text: str | None, photo: str | None) -> ChatMessage:
    if conversation.blocked_by_master and sender == "client":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Мастер ограничил этот чат")
    message = ChatMessage(conversation_id=conversation.id, sender=sender, text=text, photo=photo)
    db.add(message)
    conversation.last_message_at = utcnow()
    if sender == "client":
        first_unread = conversation.master_unread == 0
        conversation.master_unread += 1
        # One notification per burst: the master is told about the first unread message, not each one.
        if first_unread:
            preview = (text or "Фото")[:100]
            notify(db, conversation.master.user_id, "Новое сообщение", f"{conversation.client_name}: {preview}",
                   kind="message", id=conversation.id, name=conversation.client_name, message=preview)
    else:
        if conversation.client_unread == 0:
            notify_client(db, conversation.client_key_hash, "reply", id=conversation.id,
                          name=conversation.master.user.name, message=(text or "Фото")[:100])
        conversation.client_unread += 1
    db.flush()
    return message


def _master_available(master: Master | None) -> bool:
    if master is None or master.user.status != UserStatus.active:
        return False
    return master.verified or not settings.masters_require_verification


@router.post("", response_model=ConversationOut, dependencies=[Depends(start_rate_limit)])
def start_chat(data: ChatStartIn, party: Party = Depends(_client_party), db: Session = Depends(get_db)):
    """A client writes to a master (from the master's profile). Reuses the existing chat with that master."""
    master = db.get(Master, data.master_id)
    if not _master_available(master):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мастер не найден")
    conversation = (
        db.query(Conversation)
        .filter(Conversation.master_id == master.id, Conversation.client_key_hash == party.key_hash)
        .first()
    )
    if conversation is None:
        conversation = Conversation(master_id=master.id, client_key_hash=party.key_hash, client_name=data.name)
        db.add(conversation)
        db.flush()
    else:
        conversation.client_name = data.name
    message = _add_message(db, conversation, "client", data.text, None)
    db.commit()
    return _out(conversation, party, message)


@router.get("", response_model=list[ConversationOut])
def list_chats(party: Party = Depends(get_party), db: Session = Depends(get_db)):
    query = db.query(Conversation).options(selectinload(Conversation.master).selectinload(Master.user))
    query = query.filter(Conversation.master_id == party.master.id) if party.master else \
        query.filter(Conversation.client_key_hash == party.key_hash)
    conversations = query.order_by(Conversation.last_message_at.desc()).limit(200).all()
    last_by_conversation = {}
    if conversations:
        ids = [c.id for c in conversations]
        # Newest message of each conversation in one query (no N+1).
        latest = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id.in_(ids))
            .order_by(ChatMessage.conversation_id, ChatMessage.id.desc())
            .all()
        )
        for message in latest:
            last_by_conversation.setdefault(message.conversation_id, message)
    return [_out(c, party, last_by_conversation.get(c.id)) for c in conversations]


@router.get("/unread", response_model=UnreadCountOut)
def unread(party: Party = Depends(get_party), db: Session = Depends(get_db)):
    query = db.query(Conversation)
    if party.master:
        rows = query.filter(Conversation.master_id == party.master.id).with_entities(Conversation.master_unread).all()
    else:
        rows = query.filter(Conversation.client_key_hash == party.key_hash).with_entities(Conversation.client_unread).all()
    return UnreadCountOut(unread=sum(r[0] for r in rows))


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_chat(conversation_id: int, party: Party = Depends(get_party), db: Session = Depends(get_db)):
    conversation = _get_conversation(db, conversation_id, party)
    last = db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id.desc()).first()
    return _out(conversation, party, last)


@router.get("/{conversation_id}/messages", response_model=list[ChatMessageOut])
def messages(conversation_id: int, after_id: int = 0, party: Party = Depends(get_party), db: Session = Depends(get_db)):
    """New messages after `after_id` (the page polls this); reading them clears the unread counter."""
    conversation = _get_conversation(db, conversation_id, party)
    items = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation.id, ChatMessage.id > after_id)
        .order_by(ChatMessage.id)
        .limit(MAX_MESSAGES_PAGE)
        .all()
    )
    if party.master and conversation.master_unread:
        conversation.master_unread = 0
        db.commit()
    elif party.key_hash and conversation.client_unread:
        conversation.client_unread = 0
        db.commit()
    return items


@router.post("/{conversation_id}/messages", response_model=ChatMessageOut, dependencies=[Depends(message_rate_limit)])
def send_message(conversation_id: int, data: ChatMessageIn, party: Party = Depends(get_party), db: Session = Depends(get_db)):
    conversation = _get_conversation(db, conversation_id, party)
    message = _add_message(db, conversation, party.side, data.text, None)
    db.commit()
    db.refresh(message)
    return message


@router.post("/{conversation_id}/photos", response_model=ChatMessageOut, dependencies=[Depends(message_rate_limit)])
async def send_photo(conversation_id: int, file: UploadFile = File(...), party: Party = Depends(get_party),
                     db: Session = Depends(get_db)):
    conversation = _get_conversation(db, conversation_id, party)
    if conversation.blocked_by_master and party.side == "client":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Мастер ограничил этот чат")
    url = await save_upload(file, f"chats/{conversation.id}")
    message = _add_message(db, conversation, party.side, None, url)
    db.commit()
    db.refresh(message)
    return message


@router.post("/{conversation_id}/block", response_model=ConversationOut)
def block(conversation_id: int, blocked: bool = True, party: Party = Depends(get_party), db: Session = Depends(get_db)):
    """The master stops a client from writing (spam, abuse)."""
    if not party.master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только мастерам")
    conversation = _get_conversation(db, conversation_id, party)
    conversation.blocked_by_master = blocked
    db.commit()
    return get_chat(conversation_id, party, db)
