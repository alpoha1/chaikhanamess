import datetime as dt
import os
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "messenger.db"
SECRET_PREFIX = "teahouse-token"
ADMIN_LOGIN = "sedr"
ADMIN_PASSWORD = "evrey"
GENERAL_CHAT_NAME = "Чайхана"
GENERAL_CHAT_AVATAR = (
    "https://i.imgur.com/1BIpgEK.jpeg"
)

DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
CORS(app)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def token_for_user(user_id):
    return f"{SECRET_PREFIX}:{user_id}"


def parse_token(raw_token):
    if not raw_token or not raw_token.startswith(f"{SECRET_PREFIX}:"):
        return None
    try:
        return int(raw_token.split(":", 1)[1])
    except ValueError:
        return None


def auth_required(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header.replace("Bearer ", "").strip()
        user_id = parse_token(token)
        if not user_id:
            return jsonify({"error": "Нужна авторизация"}), 401

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "Пользователь не найден"}), 401

        g.current_user = user
        return handler(*args, **kwargs)

    return wrapper


def init_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT NOT NULL,
            avatar_url TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            is_group INTEGER DEFAULT 0,
            avatar_url TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            muted INTEGER DEFAULT 0,
            mute_reason TEXT,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT,
            type TEXT DEFAULT 'text',
            file_url TEXT,
            file_name TEXT,
            mime_type TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS direct_pairs (
            user_a INTEGER NOT NULL,
            user_b INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            UNIQUE(user_a, user_b)
        );
        """
    )

    admin = db.execute("SELECT * FROM users WHERE login = ?", (ADMIN_LOGIN,)).fetchone()
    if not admin:
        db.execute(
            """
            INSERT INTO users (login, password_hash, nickname, avatar_url, is_admin, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                ADMIN_LOGIN,
                generate_password_hash(ADMIN_PASSWORD),
                "Sedr",
                None,
                now_iso(),
            ),
        )
        db.commit()

    chat = db.execute(
        "SELECT * FROM chats WHERE title = ? AND is_group = 1",
        (GENERAL_CHAT_NAME,),
    ).fetchone()
    if not chat:
        admin_id = db.execute(
            "SELECT id FROM users WHERE login = ?",
            (ADMIN_LOGIN,),
        ).fetchone()["id"]
        db.execute(
            """
            INSERT INTO chats (title, is_group, avatar_url, created_by, created_at)
            VALUES (?, 1, ?, ?, ?)
            """,
            (GENERAL_CHAT_NAME, GENERAL_CHAT_AVATAR, admin_id, now_iso()),
        )
        db.commit()
        chat = db.execute(
            "SELECT * FROM chats WHERE title = ? AND is_group = 1",
            (GENERAL_CHAT_NAME,),
        ).fetchone()

    user_rows = db.execute("SELECT id FROM users").fetchall()
    for user_row in user_rows:
        db.execute(
            """
            INSERT OR IGNORE INTO chat_members (chat_id, user_id, muted, mute_reason)
            VALUES (?, ?, 0, '')
            """,
            (chat["id"], user_row["id"]),
        )
    db.commit()
    db.close()


def row_to_user(row):
    return {
        "id": row["id"],
        "login": row["login"],
        "nickname": row["nickname"],
        "avatar_url": row["avatar_url"],
        "is_admin": bool(row["is_admin"]),
    }


def get_general_chat_id(db):
    row = db.execute(
        "SELECT id FROM chats WHERE title = ? AND is_group = 1",
        (GENERAL_CHAT_NAME,),
    ).fetchone()
    return row["id"]


def ensure_general_membership(db, user_id):
    general_chat_id = get_general_chat_id(db)
    db.execute(
        """
        INSERT OR IGNORE INTO chat_members (chat_id, user_id, muted, mute_reason)
        VALUES (?, ?, 0, '')
        """,
        (general_chat_id, user_id),
    )
    db.commit()


def get_chat_participants(db, chat_id):
    rows = db.execute(
        """
        SELECT u.id, u.nickname, u.login, u.avatar_url, cm.muted
        FROM chat_members cm
        JOIN users u ON u.id = cm.user_id
        WHERE cm.chat_id = ?
        ORDER BY u.nickname COLLATE NOCASE ASC
        """,
        (chat_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "nickname": row["nickname"],
            "login": row["login"],
            "avatar_url": row["avatar_url"],
            "muted": bool(row["muted"]),
        }
        for row in rows
    ]


def get_last_message(db, chat_id):
    row = db.execute(
        """
        SELECT *
        FROM messages
        WHERE chat_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "text": row["text"],
        "type": row["type"],
        "created_at": row["created_at"],
    }


def serialize_chat(db, row, current_user_id):
    participants = get_chat_participants(db, row["id"])
    member_state = db.execute(
        "SELECT muted, mute_reason FROM chat_members WHERE chat_id = ? AND user_id = ?",
        (row["id"], current_user_id),
    ).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "is_group": bool(row["is_group"]),
        "avatar_url": row["avatar_url"],
        "participant_count": len(participants),
        "participants": participants,
        "last_message": get_last_message(db, row["id"]),
        "muted_for_me": bool(member_state["muted"]) if member_state else False,
        "mute_reason": member_state["mute_reason"] if member_state else "",
    }


def serialize_message(row, db):
    user = db.execute(
        "SELECT nickname, avatar_url FROM users WHERE id = ?",
        (row["sender_id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "sender_id": row["sender_id"],
        "sender_nickname": user["nickname"] if user else "Unknown",
        "sender_avatar": user["avatar_url"] if user else None,
        "text": row["text"] or "",
        "type": row["type"],
        "file_url": row["file_url"],
        "file_name": row["file_name"],
        "mime_type": row["mime_type"],
        "created_at": row["created_at"],
    }


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": now_iso()})


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOADS_DIR, filename)


@app.route("/register", methods=["POST"])
def register():
    db = get_db()
    payload = request.get_json(force=True)
    login = payload.get("login", "").strip().lower()
    password = payload.get("password", "").strip()
    nickname = payload.get("nickname", "").strip()

    if not login or not password or not nickname:
        return jsonify({"error": "Нужны логин, пароль и ник"}), 400

    exists = db.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
    if exists:
        return jsonify({"error": "Такой логин уже занят"}), 400

    db.execute(
        """
        INSERT INTO users (login, password_hash, nickname, avatar_url, is_admin, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (login, generate_password_hash(password), nickname, None, now_iso()),
    )
    db.commit()

    user = db.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
    ensure_general_membership(db, user["id"])
    return jsonify({"token": token_for_user(user["id"]), "user": row_to_user(user)})


@app.route("/login", methods=["POST"])
def login():
    db = get_db()
    payload = request.get_json(force=True)
    login_value = payload.get("login", "").strip().lower()
    password = payload.get("password", "").strip()
    user = db.execute("SELECT * FROM users WHERE login = ?", (login_value,)).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Неверный логин или пароль"}), 401

    ensure_general_membership(db, user["id"])
    return jsonify({"token": token_for_user(user["id"]), "user": row_to_user(user)})


@app.route("/profile", methods=["PUT"])
@auth_required
def update_profile():
    db = get_db()
    payload = request.get_json(force=True)
    nickname = payload.get("nickname", "").strip()
    avatar_url = payload.get("avatar_url")

    if not nickname:
        return jsonify({"error": "Никнейм не может быть пустым"}), 400

    db.execute(
        "UPDATE users SET nickname = ?, avatar_url = ? WHERE id = ?",
        (nickname, avatar_url, g.current_user["id"]),
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    return jsonify({"user": row_to_user(user)})


@app.route("/users", methods=["GET"])
@auth_required
def users():
    db = get_db()
    rows = db.execute(
        "SELECT id, login, nickname, avatar_url, is_admin FROM users ORDER BY nickname COLLATE NOCASE ASC"
    ).fetchall()
    return jsonify({"users": [row_to_user(row) for row in rows]})


@app.route("/chats", methods=["GET"])
@auth_required
def chats():
    db = get_db()
    rows = db.execute(
        """
        SELECT c.*
        FROM chats c
        JOIN chat_members cm ON cm.chat_id = c.id
        WHERE cm.user_id = ?
        ORDER BY (
          SELECT COALESCE(MAX(m.id), 0)
          FROM messages m
          WHERE m.chat_id = c.id
        ) DESC, c.id ASC
        """,
        (g.current_user["id"],),
    ).fetchall()
    return jsonify({"chats": [serialize_chat(db, row, g.current_user["id"]) for row in rows]})


@app.route("/direct-chat", methods=["POST"])
@auth_required
def direct_chat():
    db = get_db()
    payload = request.get_json(force=True)
    other_user_id = int(payload.get("user_id"))
    current_user_id = int(g.current_user["id"])

    if other_user_id == current_user_id:
        return jsonify({"error": "Нельзя создать диалог с собой"}), 400

    ordered = tuple(sorted([current_user_id, other_user_id]))
    pair = db.execute(
        "SELECT chat_id FROM direct_pairs WHERE user_a = ? AND user_b = ?",
        ordered,
    ).fetchone()

    if pair:
        chat = db.execute("SELECT * FROM chats WHERE id = ?", (pair["chat_id"],)).fetchone()
        return jsonify({"chat": serialize_chat(db, chat, current_user_id)})

    other_user = db.execute("SELECT * FROM users WHERE id = ?", (other_user_id,)).fetchone()
    if not other_user:
        return jsonify({"error": "Пользователь не найден"}), 404

    db.execute(
        """
        INSERT INTO chats (title, is_group, avatar_url, created_by, created_at)
        VALUES (?, 0, ?, ?, ?)
        """,
        (other_user["nickname"], other_user["avatar_url"], current_user_id, now_iso()),
    )
    db.commit()
    chat_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    db.execute(
        "INSERT INTO direct_pairs (user_a, user_b, chat_id) VALUES (?, ?, ?)",
        (*ordered, chat_id),
    )
    db.execute(
        "INSERT INTO chat_members (chat_id, user_id, muted, mute_reason) VALUES (?, ?, 0, '')",
        (chat_id, current_user_id),
    )
    db.execute(
        "INSERT INTO chat_members (chat_id, user_id, muted, mute_reason) VALUES (?, ?, 0, '')",
        (chat_id, other_user_id),
    )
    db.commit()

    chat = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return jsonify({"chat": serialize_chat(db, chat, current_user_id)})


def ensure_can_send(db, chat_id, user_id):
    member = db.execute(
        "SELECT muted FROM chat_members WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ).fetchone()
    if not member:
        return False, ("Нет доступа к чату", 403)
    if member["muted"]:
        return False, ("Вы заглушены администратором", 403)
    return True, None


def add_message(
    db,
    chat_id,
    sender_id,
    text="",
    msg_type="text",
    file_url=None,
    file_name=None,
    mime_type=None,
):
    db.execute(
        """
        INSERT INTO messages (chat_id, sender_id, text, type, file_url, file_name, mime_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (chat_id, sender_id, text, msg_type, file_url, file_name, mime_type, now_iso()),
    )
    db.commit()
    message_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()


@app.route("/messages/<int:chat_id>", methods=["GET"])
@auth_required
def get_messages(chat_id):
    db = get_db()
    member = db.execute(
        "SELECT muted, mute_reason FROM chat_members WHERE chat_id = ? AND user_id = ?",
        (chat_id, g.current_user["id"]),
    ).fetchone()
    if not member:
        return jsonify({"error": "Нет доступа к этому чату"}), 403

    chat = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    rows = db.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY id ASC",
        (chat_id,),
    ).fetchall()
    return jsonify(
        {
            "chat": {
                **serialize_chat(db, chat, g.current_user["id"]),
                "muted_for_me": bool(member["muted"]),
                "mute_reason": member["mute_reason"] or "",
            },
            "messages": [serialize_message(row, db) for row in rows],
        }
    )


@app.route("/messages/<int:chat_id>", methods=["POST"])
@auth_required
def post_message(chat_id):
    db = get_db()
    allowed, error = ensure_can_send(db, chat_id, g.current_user["id"])
    if not allowed:
        return jsonify({"error": error[0]}), error[1]

    payload = request.get_json(force=True)
    text = payload.get("text", "").strip()
    if not text:
        return jsonify({"error": "Сообщение пустое"}), 400

    message = add_message(db, chat_id, g.current_user["id"], text=text)
    return jsonify({"message": serialize_message(message, db)})


@app.route("/upload/<int:chat_id>", methods=["POST"])
@auth_required
def upload(chat_id):
    db = get_db()
    upload_type = request.form.get("type", "file")
    if upload_type != "avatar":
        allowed, error = ensure_can_send(db, chat_id, g.current_user["id"])
        if not allowed:
            return jsonify({"error": error[0]}), error[1]

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "Файл не найден"}), 400

    original_name = secure_filename(file.filename or f"file-{uuid.uuid4().hex}")
    extension = Path(original_name).suffix
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOADS_DIR / stored_name
    file.save(destination)

    public_url = f"/uploads/{stored_name}"
    mime_type = file.mimetype or "application/octet-stream"

    if upload_type == "avatar":
        return jsonify(
            {
                "file_url": public_url,
                "file_name": original_name,
                "mime_type": mime_type,
            }
        )

    message = add_message(
        db,
        chat_id,
        g.current_user["id"],
        text="",
        msg_type=upload_type,
        file_url=public_url,
        file_name=original_name,
        mime_type=mime_type,
    )
    return jsonify(
        {
            "message": serialize_message(message, db),
            "file_url": public_url,
            "file_name": original_name,
        }
    )


@app.route("/mute", methods=["POST"])
@auth_required
def mute():
    db = get_db()
    if not g.current_user["is_admin"]:
        return jsonify({"error": "Только админ может заглушать"}), 403

    payload = request.get_json(force=True)
    chat_id = int(payload.get("chat_id"))
    user_id = int(payload.get("user_id"))
    muted = 1 if bool(payload.get("muted")) else 0

    general_chat_id = get_general_chat_id(db)
    if chat_id != general_chat_id:
        return jsonify({"error": "Мут доступен только в общем чате"}), 400
    if user_id == g.current_user["id"]:
        return jsonify({"error": "Нельзя заглушить себя"}), 400

    db.execute(
        "UPDATE chat_members SET muted = ?, mute_reason = ? WHERE chat_id = ? AND user_id = ?",
        (
            muted,
            "Вам временно запрещено писать в общем чате." if muted else "",
            chat_id,
            user_id,
        ),
    )
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
else:
    init_db()
