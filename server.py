import os
import uuid
import base64
import sqlite3
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit

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
        muted INTEGER DEFAULT 0
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        type TEXT,
        text TEXT,
        file_url TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        url TEXT,
        mime TEXT
    )
    """)

ADMIN_LOGIN = "sedr"
ADMIN_PASS = "evrey"

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    login = data.get("login")
    password = data.get("password")
    nick = data.get("nick")

    if login == ADMIN_LOGIN and password != ADMIN_PASS:
        return jsonify({"ok": False, "error": "wrong admin password"})

    with db() as con:
        con.execute("INSERT OR IGNORE INTO users(login, nick) VALUES (?,?)", (login, nick))

    return jsonify({"ok": True, "user": {"login": login, "nick": nick}})

@socketio.on("connect")
def on_connect():
    # Отправляем историю
    with db() as con:
        rows = con.execute("SELECT sender, type, text, file_url FROM messages ORDER BY id ASC").fetchall()

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
def on_send(data):
    sender = data.get("from")
    text = data.get("text")

    with db() as con:
        muted = con.execute("SELECT muted FROM users WHERE login=?", (sender,)).fetchone()
        if muted and muted[0] == 1:
            return

        con.execute("INSERT INTO messages(sender, type, text) VALUES (?,?,?)",
                    (sender, "text", text))

    msg = {"type": "text", "nick": sender, "text": text}
    emit("message", msg, broadcast=True)

@socketio.on("file")
def on_file(data):
    name = data.get("name")
    b64 = data.get("data")
    mime = data.get("mime")

    raw = base64.b64decode(b64)
    os.makedirs("uploads", exist_ok=True)

    filename = f"{uuid.uuid4().hex}_{name}"
    path = f"uploads/{filename}"

    with open(path, "wb") as f:
        f.write(raw)

    url = request.host_url + "file/" + filename

    with db() as con:
        con.execute("INSERT INTO files(name, url, mime) VALUES (?,?,?)", (name, url, mime))
        con.execute("INSERT INTO messages(sender, type, file_url) VALUES (?,?,?)",
                    ("file", "file", url))

    msg = {"type": "file", "url": url, "name": name}
    emit("message", msg, broadcast=True)

@app.route("/file/<fname>")
def serve_file(fname):
    return send_file(os.path.join("uploads", fname))

@socketio.on("mute")
def mute_user(data):
    admin = data.get("admin")
    target = data.get("target")

    if admin != ADMIN_LOGIN:
        return

    with db() as con:
        con.execute("UPDATE users SET muted=1 WHERE login=?", (target,))

    emit("message", {"type": "text", "text": f"{target} был заглушён"}, broadcast=True)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
