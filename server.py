from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, join_room, send
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token
import os, base64
from datetime import datetime

# ----------- ОСНОВА -----------

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['JWT_SECRET_KEY'] = 'jwt-secret'

db = SQLAlchemy(app)
jwt = JWTManager(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ----------- БД -----------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))
    room = db.Column(db.String(50))
    text = db.Column(db.String(500))

# ----------- API -----------

@app.route('/')
def home():
    return {"msg": "server works"}

@app.route('/register', methods=['POST'])
def register():
    data = request.json

    if not data or not data.get('username') or not data.get('password'):
        return {"msg": "missing data"}, 400

    if User.query.filter_by(username=data['username']).first():
        return {"msg": "user exists"}, 400

    user = User(username=data['username'], password=data['password'])
    db.session.add(user)
    db.session.commit()

    return {"msg": "registered"}

@app.route('/login', methods=['POST'])
def login():
    data = request.json

    user = User.query.filter_by(
        username=data.get('username'),
        password=data.get('password')
    ).first()

    if user:
        token = create_access_token(identity=user.username)
        return {"token": token, "username": user.username}

    return {"msg": "bad login"}, 401

@app.route('/messages/<room>')
def get_messages(room):
    msgs = Message.query.filter_by(room=room).all()

    result = []
    for m in msgs:
        if m.text.startswith("[image]"):
            result.append({
                "user": m.username,
                "image": m.text.replace("[image]", "")
            })
        else:
            result.append({
                "user": m.username,
                "text": m.text
            })

    return jsonify(result)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

# ----------- SOCKET -----------

@socketio.on('connect')
def connect():
    print("User connected")

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('message')
def handle_message(data):
    username = data.get('username')
    room = data.get('room')
    text = data.get('message')

    if not username or not room or not text:
        return

    msg = Message(
        username=username,
        room=room,
        text=text
    )
    db.session.add(msg)
    db.session.commit()

    send({
        "user": username,
        "text": text
    }, to=room)

@socketio.on('image')
def handle_image(data):
    username = data.get('username')
    room = data.get('room')
    image_data = data.get('image')

    if not username or not room or not image_data:
        return

    filename = f"{username}_{datetime.now().timestamp()}.png"
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    with open(filepath, "wb") as f:
        f.write(base64.b64decode(image_data))

    msg = Message(
        username=username,
        room=room,
        text=f"[image]{filename}"
    )
    db.session.add(msg)
    db.session.commit()

    send({
        "user": username,
        "image": filename
    }, to=room)

@socketio.on('disconnect')
def disconnect():
    print("User disconnected")

# ----------- ЗАПУСК -----------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)





class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(100))
    avatar = db.Column(db.String(200))
