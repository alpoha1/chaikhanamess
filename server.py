from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, join_room, send
from flask_sqlalchemy import SQLAlchemy
import os, base64
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------- МОДЕЛИ --------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))

class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    chat = db.Column(db.String(50))
    text = db.Column(db.String(500))

# -------- API --------

@app.route('/')
def home():
    return {"msg": "ok"}

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(username=data['username']).first():
        return {"msg": "exists"}, 400

    user = User(username=data['username'], password=data['password'])
    db.session.add(user)
    db.session.commit()
    return {"msg": "ok"}

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['username'], password=data['password']).first()

    if user:
        return {"username": user.username}
    return {"msg": "error"}, 401

@app.route('/chats')
def chats():
    chats = Chat.query.all()
    return jsonify([{"name": c.name} for c in chats])

@app.route('/create_chat', methods=['POST'])
def create_chat():
    data = request.json
    chat = Chat(name=data['name'])
    db.session.add(chat)
    db.session.commit()
    return {"msg": "ok"}

@app.route('/messages/<chat>')
def get_messages(chat):
    msgs = Message.query.filter_by(chat=chat).all()
    result = []

    for m in msgs:
        if m.text.startswith("[img]"):
            result.append({"user": m.username, "image": m.text[5:]})
        else:
            result.append({"user": m.username, "text": m.text})

    return jsonify(result)

@app.route('/uploads/<filename>')
def upload(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

# -------- SOCKET --------

@socketio.on('join')
def join(data):
    join_room(data['chat'])

@socketio.on('message')
def msg(data):
    m = Message(username=data['user'], chat=data['chat'], text=data['text'])
    db.session.add(m)
    db.session.commit()

    send({"user": data['user'], "text": data['text']}, to=data['chat'])

@socketio.on('image')
def img(data):
    filename = f"{datetime.now().timestamp()}.png"
    path = os.path.join(UPLOAD_FOLDER, filename)

    with open(path, "wb") as f:
        f.write(base64.b64decode(data['image']))

    m = Message(username=data['user'], chat=data['chat'], text=f"[img]{filename}")
    db.session.add(m)
    db.session.commit()

    send({"user": data['user'], "image": filename}, to=data['chat'])

# -------- ЗАПУСК --------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
