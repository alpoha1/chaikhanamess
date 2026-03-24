# server.py (Flask + Flask-SocketIO)
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit, join_room, leave_room
import base64, os, uuid

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
USERS = {}  # login -> {nick, muted:bool}
MESSAGES = []  # simple history
GROUP = {'id':'chaihana','name':'чайхана','members':set(), 'admin':'sedr'}

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    login = data.get('login'); pwd = data.get('password'); nick = data.get('nick','')
    USERS[login] = {'nick':nick or login, 'muted': False}
    GROUP['members'].add(login)
    return jsonify({'ok':True,'user':{'login':login,'nick':USERS[login]['nick']}})

@socketio.on('connect')
def on_connect():
    emit('chats',[GROUP])

@socketio.on('send')
def on_send(data):
    sender = data.get('from')
    if USERS.get(sender,{}).get('muted'): return
    msg = {'type':'text','text':data.get('text'),'nick':USERS.get(sender,{}).get('nick')}
    MESSAGES.append(msg)
    emit('message', msg, broadcast=True)

@socketio.on('file')
def on_file(data):
    name = data.get('name'); b64 = data.get('data'); mime = data.get('mime','application/octet-stream')
    raw = base64.b64decode(b64)
    fn = f'uploads/{uuid.uuid4().hex}_{name}'
    os.makedirs('uploads', exist_ok=True)
    with open(fn,'wb') as f: f.write(raw)
    url = request.host_url + 'file/' + os.path.basename(fn)
    msg = {'type':'file','url':url,'name':name}
    MESSAGES.append(msg)
    emit('message', msg, broadcast=True)

@app.route('/file/<fname>')
def serve_file(fname):
    return send_file(os.path.join('uploads', fname))

@socketio.on('mute')
def on_mute(data):
    admin = data.get('admin'); target = data.get('target')
    if admin==GROUP['admin']:
        USERS[target]['muted'] = True
        emit('message', {'type':'text','text':f'{target} muted by admin'}, broadcast=True)

if __name__=='__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
