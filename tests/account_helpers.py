import sqlite3
from werkzeug.security import generate_password_hash


def signed_client(app, role='coordinator', record_id=None, username=None):
    username = username or role
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('INSERT OR IGNORE INTO accounts (username,password_hash,role,record_id) VALUES (?,?,?,?)',
                   (username, generate_password_hash('test-password-123', method='pbkdf2:sha256:1000'), role, record_id))
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf']
    response = client.post('/login', data={'username':username, 'password':'test-password-123', 'csrf_token':token})
    assert response.status_code == 302
    with client.session_transaction() as session:
        client.environ_base['HTTP_X_CSRF_TOKEN'] = session['csrf']
    return client
