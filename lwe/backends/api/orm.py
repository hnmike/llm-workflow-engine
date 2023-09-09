import datetime

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlite3 import Connection as SQLite3Connection
from sqlalchemy import MetaData, ForeignKey, Index, Column, Integer, String, DateTime, JSON, Boolean
from sqlalchemy import desc
from sqlalchemy.orm import relationship
from sqlalchemy.orm import declarative_base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy import inspect

from lwe.core.config import Config
from lwe.core.logger import Logger
import lwe.core.constants as constants

Base = declarative_base()


def _set_sqlite_pragma(conn, _record):
    if isinstance(conn, SQLite3Connection):
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


event.listen(Engine, "connect", _set_sqlite_pragma)


class User(Base):
    __tablename__ = 'user'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=True)
    email = Column(String, unique=True, nullable=True)
    default_preset = Column(String, nullable=False)
    created_time = Column(DateTime, nullable=False)
    last_login_time = Column(DateTime, nullable=True)
    preferences = Column(JSON, nullable=False)

    conversations = relationship('Conversation', back_populates='user', passive_deletes=True)


Index('user_username_idx', User.username)
Index('user_email_idx', User.email)
Index('user_created_time_idx', User.created_time)
Index('user_last_login_time', User.last_login_time)


class Conversation(Base):
    __tablename__ = 'conversation'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    title = Column(String, nullable=True)
    created_time = Column(DateTime, nullable=False)
    updated_time = Column(DateTime, nullable=False)
    hidden = Column(Boolean, nullable=False)

    user = relationship('User', back_populates='conversations')
    messages = relationship('Message', back_populates='conversation', passive_deletes=True)


Index('conversation_user_id_idx', Conversation.user_id)
Index('conversation_created_time_idx', Conversation.created_time)
Index('conversation_updated_time_idx', Conversation.updated_time)
Index('conversation_hidden_idx', Conversation.hidden)


class Message(Base):
    __tablename__ = 'message'

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey('conversation.id', ondelete='CASCADE'), nullable=False)
    role = Column(String, nullable=False)
    message = Column(String, nullable=False)
    message_type = Column(String, nullable=False)
    message_metadata = Column(String)
    model = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    preset = Column(String, nullable=False)
    created_time = Column(DateTime, nullable=False)

    conversation = relationship('Conversation', back_populates='messages')


Index('message_conversation_id_idx', Message.conversation_id)
Index('message_created_time_idx', Message.created_time)


class Orm:
    def __init__(self, config=None):
        self.config = config or Config()
        self.log = Logger(self.__class__.__name__, self.config)
        self.database = self.config.get('database')
        self.engine, self.metadata = self.create_engine_and_metadata()
        self.session = scoped_session(sessionmaker(bind=self.engine))

    def create_engine_and_metadata(self):
        args = ""
        # TODO: check_same_thread is currently needed for SQLite so the
        # separate thread that generates titles can run without error.
        # It would probably be better to work this out with locking or
        # a separate database connection or other fix.
        if self.database.startswith('sqlite'):
            args = "?check_same_thread=False"
        engine = create_engine(f"{self.database}{args}")
        metadata = MetaData()
        metadata.reflect(bind=engine)
        return engine, metadata

    def object_as_dict(self, obj):
        return {c.key: getattr(obj, c.key)
                for c in inspect(obj).mapper.column_attrs}


class Manager:
    def __init__(self, config=None, orm=None):
        self.config = config or Config()
        self.log = Logger(self.__class__.__name__, self.config)
        self.orm = orm or Orm(self.config)
        self.session = self.orm.session()

    def _handle_error(self, message):
        self.log.error(message)
        return False, None, message

    def _apply_limit_offset(self, query, limit, offset):
        if limit is not None:
            query = query.limit(limit)
        if offset is not None:
            query = query.offset(offset)
        return query

    def orm_get_users(self, limit=None, offset=None):
        self.log.debug('Retrieving all Users')
        query = self.session.query(User).order_by(User.username)
        query = self._apply_limit_offset(query, limit, offset)
        users = query.all()
        return users

    def orm_get_conversations(self, user, limit=constants.DEFAULT_HISTORY_LIMIT, offset=None, order_desc=True):
        self.log.debug(f'Retrieving Conversations for User with id {user.id}')
        if order_desc:
            query = self.session.query(Conversation).filter(Conversation.user_id == user.id).order_by(desc(Conversation.id))
        else:
            query = self.session.query(Conversation).order_by(Conversation.id)
        query = self._apply_limit_offset(query, limit, offset)
        conversations = query.all()
        return conversations

    def orm_get_messages(self, conversation, limit=None, offset=None, target_id=None):
        self.log.debug(f'Retrieving Messages for Conversation with id {conversation.id}')
        query = self.session.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.id)
        query = self._apply_limit_offset(query, limit, offset)
        if target_id:
            query = query.filter(Message.id <= target_id)
        messages = query.all()
        return messages

    def orm_get_last_message(self, conversation):
        self.log.debug(f'Retrieving last Message for Conversation with id {conversation.id}')
        query = self.session.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.id.desc()).limit(1)
        last_message = query.first()
        return last_message

    def orm_add_user(self, username, password, email, default_preset="", preferences=None):
        preferences = preferences or {}
        now = datetime.datetime.now()
        user = User(username=username, password=password, email=email, default_preset=default_preset, created_time=now, last_login_time=now, preferences=preferences)
        self.session.add(user)
        self.session.commit()
        self.log.info(f'Added User with username {username}')
        return user

    def orm_add_conversation(self, user, title, hidden=False):
        now = datetime.datetime.now()
        conversation = Conversation(user_id=user.id, title=title, created_time=now, updated_time=now, hidden=False)
        self.session.add(conversation)
        self.session.commit()
        self.log.info(f"Added Conversation with title: {title} for User {user.username}")
        return conversation

    def orm_add_message(self, conversation, role, message, message_type, message_metadata, provider, model, preset):
        now = datetime.datetime.now()
        message = Message(conversation_id=conversation.id, role=role, message=message, message_type=message_type, message_metadata=message_metadata, provider=provider, model=model, preset=preset, created_time=now)
        self.session.add(message)
        # Original conversation was created in another session, so load one fresh.
        conversation_update = self.orm_get_conversation(conversation.id)
        setattr(conversation_update, 'updated_time', now)
        self.session.commit()
        self.log.info(f"Added Message with role: {role}, message_type: {message_type}, message_metadata: {message_metadata}, provider: {provider}, model: {model}, preset: {preset} for Conversation with id {conversation.id}")
        return message

    def orm_get_user(self, user_id):
        self.log.debug(f'Retrieving User with id {user_id}')
        user = self.session.get(User, user_id)
        return user

    def orm_get_conversation(self, conversation_id):
        self.log.debug(f'Retrieving Conversation with id {conversation_id}')
        conversation = self.session.get(Conversation, conversation_id)
        return conversation

    def orm_get_message(self, message_id):
        self.log.debug(f'Retrieving Message with id {message_id}')
        message = self.session.get(Message, message_id)
        return message

    def orm_edit_user(self, user, **kwargs):
        for key, value in kwargs.items():
            setattr(user, key, value)
        self.session.commit()
        self.log.info(f'Edited User with id {user.id}')
        return user

    def orm_edit_conversation(self, conversation, **kwargs):
        for key, value in kwargs.items():
            setattr(conversation, key, value)
        self.session.commit()
        self.log.info(f'Edited Conversation with id {conversation.id}: {kwargs}')
        return conversation

    def orm_edit_message(self, message, **kwargs):
        for key, value in kwargs.items():
            setattr(message, key, value)
        self.session.commit()
        self.log.info(f'Edited Message with id {message.id}')
        return message

    def orm_delete_user(self, user):
        self.session.delete(user)
        self.session.commit()
        self.log.info(f'Deleted User with id {user.id}')
        return user

    def orm_delete_conversation(self, conversation):
        self.session.delete(conversation)
        self.session.commit()
        self.log.info(f'Deleted Conversation with id {conversation.id}')

    def orm_delete_message(self, message):
        self.session.delete(message)
        self.session.commit()
        self.log.info(f'Deleted Message with id {message.id}')
