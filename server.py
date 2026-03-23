from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, join_room, send
from flask_sqlalchemy import SQLAlchemy
import os, base64, time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.db'

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------- БД --------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(50))
    avatar = db.Column(db.String(200))

class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(50))
    chat = db.Column(db.String(50))
    text = db.Column(db.String(500))

# -------- API --------

@app.route('/')
def home():
    return {"msg":"ok"}

@app.route('/register', methods=['POST'])
def register():
    d=request.json
    if User.query.filter_by(username=d['username']).first():
        return {"msg":"exists"},400

    db.session.add(User(username=d['username'],password=d['password']))
    db.session.commit()
    return {"msg":"ok"}

@app.route('/login', methods=['POST'])
def login():
    d=request.json
    u=User.query.filter_by(username=d['username'],password=d['password']).first()
    if u:
        return {"msg":"ok"}
    return {"msg":"error"},401

@app.route('/update_profile', methods=['POST'])
def update_profile():
    d=request.json
    u=User.query.filter_by(username=d['old']).first()

    if not u:
        return {"msg":"error"},400

    u.username = d['new']
    db.session.commit()
    return {"msg":"ok"}

@app.route('/chats')
def chats():
    return jsonify([{"name":c.name} for c in Chat.query.all()])

@app.route('/create_chat', methods=['POST'])
def create_chat():
    d=request.json
    db.session.add(Chat(name=d['name']))
    db.session.commit()
    return {"msg":"ok"}

@app.route('/messages/<chat>')
def messages(chat):
    msgs=Message.query.filter_by(chat=chat).all()
    res=[]
    for m in msgs:
        if m.text.startswith("[img]"):
            res.append({"user":m.user,"image":m.text[5:]})
        else:
            res.append({"user":m.user,"text":m.text})
    return jsonify(res)

@app.route('/upload/<f>')
def upload(f):
    return send_file(os.path.join(UPLOAD_FOLDER,f))

@app.route('/avatar/<username>')
def avatar(username):
    u=User.query.filter_by(username=username).first()
    if u and u.avatar:
        return send_file(os.path.join(UPLOAD_FOLDER,u.avatar))
    return {"msg":"no avatar"}

@app.route('/set_avatar', methods=['POST'])
def set_avatar():
    d=request.json
    filename=d['username']+"_avatar.png"
    path=os.path.join(UPLOAD_FOLDER,filename)

    with open(path,"wb") as f:
        f.write(base64.b64decode(d['image']))

    u=User.query.filter_by(username=d['username']).first()
    u.avatar=filename
    db.session.commit()

    return {"msg":"ok"}

# -------- SOCKET --------

@socketio.on('join')
def join(d):
    join_room(d['chat'])

@socketio.on('message')
def msg(d):
    m=Message(user=d['user'],chat=d['chat'],text=d['text'])
    db.session.add(m)
    db.session.commit()
    send({"user":d['user'],"text":d['text']},to=d['chat'])

@socketio.on('image')
def img(d):
    name=str(time.time())+".png"
    path=os.path.join(UPLOAD_FOLDER,name)

    with open(path,"wb") as f:
        f.write(base64.b64decode(d['image']))

    m=Message(user=d['user'],chat=d['chat'],text="[img]"+name)
    db.session.add(m)
    db.session.commit()

    send({"user":d['user'],"image":name},to=d['chat'])

# -------- ЗАПУСК --------

if __name__=="__main__":
    with app.app_context():
        db.create_all()

    socketio.run(app,host="0.0.0.0",port=5000)
