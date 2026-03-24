from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import json, os
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

USERS_FILE = "users.json"
CHATS_FILE = "chats.json"

def load(file):
    if not os.path.exists(file):
        return {}
    with open(file, "r") as f:
        return json.load(f)

def save(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

# --- INIT ---
def init():
    chats = load(CHATS_FILE)
    if "chayhana" not in chats:
        chats["chayhana"] = {
            "name": "Чайхана",
            "users": [],
            "messages": []
        }
        save(CHATS_FILE, chats)

init()

# --- AUTH ---
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    users = load(USERS_FILE)
    chats = load(CHATS_FILE)

    if data["login"] in users:
        return jsonify({"error": "exists"})

    users[data["login"]] = {
        "password": data["password"],
        "nickname": data["login"],
        "avatar": "",
        "muted": False
    }

    chats["chayhana"]["users"].append(data["login"])

    save(USERS_FILE, users)
    save(CHATS_FILE, chats)
    return jsonify({"status": "ok"})

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    users = load(USERS_FILE)

    user = users.get(data["login"])
    if not user or user["password"] != data["password"]:
        return jsonify({"error": "wrong"})

    return jsonify(user)

# --- USERS ---
@app.route("/users")
def users_list():
    return jsonify(load(USERS_FILE))

# --- PROFILE ---
@app.route("/profile", methods=["POST"])
def profile():
    data = request.json
    users = load(USERS_FILE)

    users[data["login"]]["nickname"] = data["nickname"]
    users[data["login"]]["avatar"] = data["avatar"]

    save(USERS_FILE, users)
    return jsonify({"status": "ok"})

# --- CHATS ---
@app.route("/chats/<login>")
def chats(login):
    chats = load(CHATS_FILE)
    result = []

    for cid, c in chats.items():
        if login in c["users"]:
            result.append({
                "id": cid,
                "name": c["name"],
                "count": len(c["users"])
            })

    return jsonify(result)

@app.route("/create_private", methods=["POST"])
def create_private():
    data = request.json
    chats = load(CHATS_FILE)

    cid = f"dm_{data['user1']}_{data['user2']}"

    if cid not in chats:
        chats[cid] = {
            "name": f"{data['user2']}",
            "users": [data["user1"], data["user2"]],
            "messages": []
        }

    save(CHATS_FILE, chats)
    return jsonify({"id": cid})

# --- FILE ---
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]
    name = secure_filename(file.filename)

    path = os.path.join(UPLOAD_FOLDER, name)
    file.save(path)

    return jsonify({"url": "/file/" + name})

@app.route("/file/<name>")
def file(name):
    return send_from_directory(UPLOAD_FOLDER, name)

# --- MESSAGES ---
@app.route("/messages/<chat>")
def messages(chat):
    return jsonify(load(CHATS_FILE)[chat]["messages"])

@socketio.on("send_message")
def send_message(data):
    chats = load(CHATS_FILE)
    users = load(USERS_FILE)

    if users[data["login"]]["muted"]:
        return

    msg = {
        "user": data["login"],
        "text": data.get("text", ""),
        "file": data.get("file", ""),
        "type": data.get("type", "text")
    }

    chats[data["chat"]]["messages"].append(msg)
    save(CHATS_FILE, chats)

    emit("receive_message", msg, broadcast=True)

# --- MUTE ---
@app.route("/mute", methods=["POST"])
def mute():
    data = request.json
    users = load(USERS_FILE)

    if data["admin"] != "sedr":
        return jsonify({"error": "no access"})

    users[data["target"]]["muted"] = data["mute"]
    save(USERS_FILE, users)

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
