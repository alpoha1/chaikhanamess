import datetime as dt
import json
import os
import sqlite3
import uuid
from functools import wraps
from pathlib import Path
from urllib import request as urllib_request

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "messenger.db"
SECRET_PREFIX = "teahouse-token"
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
ADMIN_NICKNAME = os.environ.get("ADMIN_NICKNAME", "Admin").strip() or "Admin"
GENERAL_CHAT_NAME = "Р§Р°Р№С…Р°РЅР°"
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
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def token_for_user(user_id):
    return f"{SECRET_PREFIX}:{user_id}"


def parse_token(raw_token):
    if not raw_token or not raw_token.startswith(f"{SECRET_PREFIX}:"):
        return None
    try:
        return int(raw_token.split(":", 1)[1])
    except ValueError:
        return None


def parse_required_int(payload, field_name):
    value = payload.get(field_name)
    if value is None or value == "":
        raise ValueError(f"Field '{field_name}' is required")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Field '{field_name}' must be an integer") from error


def auth_required(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header.replace("Bearer ", "").strip()
        user_id = parse_token(token)
        if not user_id:
            return jsonify({"error": "РќСѓР¶РЅР° Р°РІС‚РѕСЂРёР·Р°С†РёСЏ"}), 401

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ"}), 401

        g.current_user = user
        return handler(*args, **kwargs)

    return wrapper


def init_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
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

        CREATE TABLE IF NOT EXISTS push_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            platform TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )

    admin = None
    if ADMIN_LOGIN and ADMIN_PASSWORD:
        admin = db.execute("SELECT * FROM users WHERE login = ?", (ADMIN_LOGIN,)).fetchone()
    if ADMIN_LOGIN and ADMIN_PASSWORD and not admin:
        db.execute(
            """
            INSERT INTO users (login, password_hash, nickname, avatar_url, is_admin, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                ADMIN_LOGIN,
                generate_password_hash(ADMIN_PASSWORD),
                ADMIN_NICKNAME,
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
        admin_id = None
        if ADMIN_LOGIN and ADMIN_PASSWORD:
            admin_row = db.execute(
                "SELECT id FROM users WHERE login = ?",
                (ADMIN_LOGIN,),
            ).fetchone()
            admin_id = admin_row["id"] if admin_row else None
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
        "sender_id": row["sender_id"],
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


def get_push_tokens_for_chat(db, chat_id, sender_id):
    rows = db.execute(
        """
        SELECT DISTINCT pt.token
        FROM push_tokens pt
        JOIN chat_members cm ON cm.user_id = pt.user_id
        WHERE cm.chat_id = ? AND pt.user_id != ?
        """,
        (chat_id, sender_id),
    ).fetchall()
    return [row["token"] for row in rows]


def send_push_notifications(tokens, title, body, data=None):
    if not tokens:
        return

    messages = [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data or {},
        }
        for token in tokens
    ]

    req = urllib_request.Request(
        "https://exp.host/--/api/v2/push/send",
        data=json.dumps(messages).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Accept-encoding": "gzip, deflate",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10):
            pass
    except Exception:
        # Push delivery failures should not break the main API flow.
        pass


def notify_chat_members(db, chat_id, sender_id, message_row):
    tokens = get_push_tokens_for_chat(db, chat_id, sender_id)
    if not tokens:
        return

    chat = db.execute("SELECT title, is_group FROM chats WHERE id = ?", (chat_id,)).fetchone()
    sender = db.execute("SELECT nickname FROM users WHERE id = ?", (sender_id,)).fetchone()
    sender_name = sender["nickname"] if sender else "Someone"
    chat_title = chat["title"] if chat else "New message"
    msg_type = message_row["type"]
    text = (message_row["text"] or "").strip()

    if text:
        body = text
    elif msg_type == "image":
        body = f"{sender_name}: photo"
    elif msg_type == "video":
        body = f"{sender_name}: video"
    elif msg_type == "file":
        body = f"{sender_name}: file"
    else:
        body = f"{sender_name}: new message"

    title = chat_title if chat and chat["is_group"] else sender_name
    send_push_notifications(tokens, title, body, {"chatId": chat_id})


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": now_iso()})


@app.route("/")
def index():
    return jsonify(
        {
            "ok": True,
            "service": "chaikhana-messenger",
            "health": "/health",
        }
    )


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
        return jsonify({"error": "РќСѓР¶РЅС‹ Р»РѕРіРёРЅ, РїР°СЂРѕР»СЊ Рё РЅРёРє"}), 400

    exists = db.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
    if exists:
        return jsonify({"error": "РўР°РєРѕР№ Р»РѕРіРёРЅ СѓР¶Рµ Р·Р°РЅСЏС‚"}), 400

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
        return jsonify({"error": "РќРµРІРµСЂРЅС‹Р№ Р»РѕРіРёРЅ РёР»Рё РїР°СЂРѕР»СЊ"}), 401

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
        return jsonify({"error": "РќРёРєРЅРµР№Рј РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ РїСѓСЃС‚С‹Рј"}), 400

    db.execute(
        "UPDATE users SET nickname = ?, avatar_url = ? WHERE id = ?",
        (nickname, avatar_url, g.current_user["id"]),
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.current_user["id"],)).fetchone()
    return jsonify({"user": row_to_user(user)})


@app.route("/push-token", methods=["PUT"])
@auth_required
def register_push_token():
    db = get_db()
    payload = request.get_json(force=True)
    token = str(payload.get("token", "")).strip()
    platform = str(payload.get("platform", "")).strip()

    if not token:
        return jsonify({"error": "Token is required"}), 400

    db.execute(
        """
        INSERT INTO push_tokens (token, user_id, platform, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            user_id = excluded.user_id,
            platform = excluded.platform,
            updated_at = excluded.updated_at
        """,
        (token, g.current_user["id"], platform, now_iso()),
    )
    db.commit()
    return jsonify({"ok": True})


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
    try:
        other_user_id = parse_required_int(payload, "user_id")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    current_user_id = int(g.current_user["id"])

    if other_user_id == current_user_id:
        return jsonify({"error": "РќРµР»СЊР·СЏ СЃРѕР·РґР°С‚СЊ РґРёР°Р»РѕРі СЃ СЃРѕР±РѕР№"}), 400

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
        return jsonify({"error": "РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ"}), 404

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
        return False, ("РќРµС‚ РґРѕСЃС‚СѓРїР° Рє С‡Р°С‚Сѓ", 403)
    if member["muted"]:
        return False, ("Р’С‹ Р·Р°РіР»СѓС€РµРЅС‹ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј", 403)
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
        return jsonify({"error": "РќРµС‚ РґРѕСЃС‚СѓРїР° Рє СЌС‚РѕРјСѓ С‡Р°С‚Сѓ"}), 403

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
        return jsonify({"error": "РЎРѕРѕР±С‰РµРЅРёРµ РїСѓСЃС‚РѕРµ"}), 400

    message = add_message(db, chat_id, g.current_user["id"], text=text)
    notify_chat_members(db, chat_id, g.current_user["id"], message)
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
        return jsonify({"error": "Р¤Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ"}), 400

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
    notify_chat_members(db, chat_id, g.current_user["id"], message)
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
        return jsonify({"error": "РўРѕР»СЊРєРѕ Р°РґРјРёРЅ РјРѕР¶РµС‚ Р·Р°РіР»СѓС€Р°С‚СЊ"}), 403

    payload = request.get_json(force=True)
    try:
        chat_id = parse_required_int(payload, "chat_id")
        user_id = parse_required_int(payload, "user_id")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    muted = 1 if bool(payload.get("muted")) else 0

    general_chat_id = get_general_chat_id(db)
    if chat_id != general_chat_id:
        return jsonify({"error": "РњСѓС‚ РґРѕСЃС‚СѓРїРµРЅ С‚РѕР»СЊРєРѕ РІ РѕР±С‰РµРј С‡Р°С‚Рµ"}), 400
    if user_id == g.current_user["id"]:
        return jsonify({"error": "РќРµР»СЊР·СЏ Р·Р°РіР»СѓС€РёС‚СЊ СЃРµР±СЏ"}), 400

    db.execute(
        "UPDATE chat_members SET muted = ?, mute_reason = ? WHERE chat_id = ? AND user_id = ?",
        (
            muted,
            "Р’Р°Рј РІСЂРµРјРµРЅРЅРѕ Р·Р°РїСЂРµС‰РµРЅРѕ РїРёСЃР°С‚СЊ РІ РѕР±С‰РµРј С‡Р°С‚Рµ." if muted else "",
            chat_id,
            user_id,
        ),
    )
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(host="0.0.0.0", port=port, debug=debug)
else:
    init_db()

