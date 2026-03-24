import os
import uuid
import base64
import sqlite3
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

DB = "database.db"

def db():
    return sqlite3.connect(DB, check_same_thread=False)

# Создание таблиц
with db() as con:
    con.execute("""
    CREATE TABLE IF NOT EXISTS users (
        login TEXT PRIMARY KEY,
        nick TEXT,
        avatar TEXT,
        muted INTEGER DEFAULT 0
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        sender TEXT,
        type TEXT,
        text TEXT,
        file_url TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        name TEXT,
        avatar TEXT,
        is_group INTEGER
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS chat_members (
        chat_id TEXT,
        login TEXT
    )
    """)

# Создаём группу "чайхана"
with db() as con:
    con.execute("""
    INSERT OR IGNORE INTO chats(id, name, avatar, is_group)
    VALUES ('chaihana', 'чайхана', 'https://i.imgur.com/8fKQZQp.jpeg', 1)
    """)

ADMIN_LOGIN = "sedr"
ADMIN_PASS = "evrey"

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    login = data["login"]
    password = data["password"]
    nick = data["nick"]
    avatar = data.get("avatar", "")

    if login == ADMIN_LOGIN and password != ADMIN_PASS:
        return jsonify({"ok": False, "error": "wrong admin password"})

    with db() as con:
        con.execute("INSERT OR IGNORE INTO users(login, nick, avatar) VALUES (?,?,?)",
                    (login, nick, avatar))
        con.execute("INSERT OR IGNORE INTO chat_members(chat_id, login) VALUES ('chaihana',?)",
                    (login,))

    return jsonify({"ok": True, "user": {"login": login, "nick": nick, "avatar": avatar}})

@socketio.on("connect")
def on_connect():
    emit("connected", {"ok": True})

@socketio.on("load_chats")
def load_chats(data):
    login = data["login"]

    with db() as con:
        rows = con.execute("""
            SELECT chats.id, chats.name, chats.avatar, chats.is_group
            FROM chats
            JOIN chat_members ON chats.id = chat_members.chat_id
            WHERE chat_members.login=?
        """, (login,)).fetchall()

    chats = []
    for cid, name, avatar, is_group in rows:
        chats.append({"id": cid, "name": name, "avatar": avatar, "is_group": is_group})

    emit("chats", chats)

@socketio.on("load_history")
def load_history(data):
    chat_id = data["chat_id"]

    with db() as con:
        rows = con.execute("""
            SELECT sender, type, text, file_url
            FROM messages
            WHERE chat_id=?
            ORDER BY id ASC
        """, (chat_id,)).fetchall()

    history = []
    for sender, type_, text, file_url in rows:
        history.append({
            "type": type_,
            "nick": sender,
            "text": text,
            "url": file_url
        })

    emit("history", history)

@socketio.on("send")
def send_msg(data):
    chat_id = data["chat_id"]
    sender = data["from"]
    text = data["text"]

    with db() as con:
        muted = con.execute("SELECT muted FROM users WHERE login=?", (sender,)).fetchone()
        if muted and muted[0] == 1:
            return

        con.execute("""
            INSERT INTO messages(chat_id, sender, type, text)
            VALUES (?,?,?,?)
        """, (chat_id, sender, "text", text))

    msg = {"type": "text", "nick": sender, "text": text}
    emit("message", msg, room=chat_id)

@socketio.on("join_chat")
def join_chat(data):
    chat_id = data["chat_id"]
    join_room(chat_id)

@socketio.on("file")
def on_file(data):
    chat_id = data["chat_id"]
    name = data["name"]
    b64 = data["data"]
    # Добавь поле отправителя в emit с фронтенда или вытащи из данных
    sender = data.get("from", "System") 

    raw = base64.b64decode(b64)
    os.makedirs("uploads", exist_ok=True)

    filename = f"{uuid.uuid4().hex}_{name}"
    path = f"uploads/{filename}"

    with open(path, "wb") as f:
        f.write(raw)

    # На некоторых хостингах (типа Render) лучше использовать прямую ссылку
    url = f"{request.host_url}file/{filename}"

    with db() as con:
        con.execute("""
            INSERT INTO messages(chat_id, sender, type, file_url)
            VALUES (?,?,?,?)
        """, (chat_id, sender, "file", url))

    msg = {"type": "file", "url": url, "nick": sender} # Добавляем nick
    emit("message", msg, room=chat_id)

@app.route("/file/<fname>")
def serve_file(fname):
    return send_file(os.path.join("uploads", fname))

@socketio.on("mute")
def mute_user(data):
    admin = data["admin"]
    target = data["target"]

    if admin != ADMIN_LOGIN:
        return

    with db() as con:
        con.execute("UPDATE users SET muted=1 WHERE login=?", (target,))

    emit("message", {"type": "text", "text": f"{target} был заглушён"}, broadcast=True)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
