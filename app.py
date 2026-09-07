import sqlite3
import os
from datetime import datetime
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)
DB_PATH = '/app/data/timetracker.db'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0
            )
        ''')
        try:
            conn.execute('ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
        except Exception:
            pass
        conn.execute('''
            CREATE TABLE IF NOT EXISTS time_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        ''')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/projects', methods=['GET'])
def get_projects():
    with get_db() as conn:
        rows = conn.execute('SELECT id, name, archived FROM projects ORDER BY name').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/projects/<int:project_id>', methods=['PUT'])
def update_project(project_id):
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return jsonify({'error': '案件名を入力してください'}), 400
    try:
        with get_db() as conn:
            conn.execute('UPDATE projects SET name = ? WHERE id = ?', (name, project_id))
        return jsonify({'updated': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': '同じ名前の案件が既に存在します'}), 409


@app.route('/api/projects/<int:project_id>/archive', methods=['POST'])
def toggle_archive(project_id):
    with get_db() as conn:
        row = conn.execute('SELECT archived FROM projects WHERE id = ?', (project_id,)).fetchone()
        if not row:
            return jsonify({'error': '案件が見つかりません'}), 404
        new_state = 0 if row['archived'] else 1
        conn.execute('UPDATE projects SET archived = ? WHERE id = ?', (new_state, project_id))
    return jsonify({'archived': bool(new_state)})


@app.route('/api/projects', methods=['POST'])
def add_project():
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return jsonify({'error': '案件名を入力してください'}), 400
    try:
        with get_db() as conn:
            cursor = conn.execute(
                'INSERT INTO projects (name, created_at) VALUES (?, ?)',
                (name, datetime.utcnow().isoformat())
            )
        return jsonify({'id': cursor.lastrowid, 'name': name}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': '同じ名前の案件が既に存在します'}), 409


@app.route('/api/current', methods=['GET'])
def get_current():
    with get_db() as conn:
        row = conn.execute('''
            SELECT tr.id, tr.project_id, p.name as project_name, tr.start_time
            FROM time_records tr
            JOIN projects p ON tr.project_id = p.id
            WHERE tr.end_time IS NULL
            ORDER BY tr.id DESC LIMIT 1
        ''').fetchone()
    return jsonify(dict(row) if row else None)


@app.route('/api/start/<int:project_id>', methods=['POST'])
def start_timer(project_id):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute('UPDATE time_records SET end_time = ? WHERE end_time IS NULL', (now,))
        cursor = conn.execute(
            'INSERT INTO time_records (project_id, start_time) VALUES (?, ?)',
            (project_id, now)
        )
    return jsonify({'id': cursor.lastrowid, 'project_id': project_id, 'start_time': now})


@app.route('/api/stop', methods=['POST'])
def stop_timer():
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute('UPDATE time_records SET end_time = ? WHERE end_time IS NULL', (now,))
    return jsonify({'stopped': True})


@app.route('/api/records', methods=['GET'])
def get_records():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT tr.id,
                   p.name as project_name,
                   DATE(tr.start_time) as date,
                   tr.start_time,
                   tr.end_time,
                   CAST((julianday(tr.end_time) - julianday(tr.start_time)) * 86400 AS INTEGER) as duration_seconds
            FROM time_records tr
            JOIN projects p ON tr.project_id = p.id
            WHERE tr.end_time IS NOT NULL
            ORDER BY tr.start_time DESC
            LIMIT 200
        ''').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def delete_record(record_id):
    with get_db() as conn:
        conn.execute('DELETE FROM time_records WHERE id = ?', (record_id,))
    return jsonify({'deleted': True})


@app.route('/api/records/<int:record_id>', methods=['PUT'])
def update_record(record_id):
    data = request.json or {}
    start_time = data.get('start_time', '').strip()
    end_time = data.get('end_time', '').strip()
    if not start_time or not end_time:
        return jsonify({'error': '開始・終了時刻を入力してください'}), 400
    if start_time >= end_time:
        return jsonify({'error': '終了時刻は開始時刻より後にしてください'}), 400
    with get_db() as conn:
        conn.execute(
            'UPDATE time_records SET start_time = ?, end_time = ? WHERE id = ?',
            (start_time, end_time, record_id)
        )
    return jsonify({'updated': True})


@app.route('/api/monthly', methods=['GET'])
def get_monthly():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT p.name as project_name,
                   strftime('%Y-%m', tr.start_time) as month,
                   CAST(SUM((julianday(tr.end_time) - julianday(tr.start_time)) * 86400) AS INTEGER) as total_seconds
            FROM time_records tr
            JOIN projects p ON tr.project_id = p.id
            WHERE tr.end_time IS NOT NULL
            GROUP BY p.id, p.name, strftime('%Y-%m', tr.start_time)
            ORDER BY p.name, month DESC
        ''').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/daily', methods=['GET'])
def get_daily():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT DATE(tr.start_time) as date,
                   p.name as project_name,
                   CAST(SUM((julianday(tr.end_time) - julianday(tr.start_time)) * 86400) AS INTEGER) as total_seconds
            FROM time_records tr
            JOIN projects p ON tr.project_id = p.id
            WHERE tr.end_time IS NOT NULL
            GROUP BY DATE(tr.start_time), p.id, p.name
            ORDER BY date DESC, total_seconds DESC
        ''').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/summary', methods=['GET'])
def get_summary():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT p.name as project_name,
                   CAST(SUM((julianday(tr.end_time) - julianday(tr.start_time)) * 86400) AS INTEGER) as total_seconds
            FROM time_records tr
            JOIN projects p ON tr.project_id = p.id
            WHERE tr.end_time IS NOT NULL
            GROUP BY p.id, p.name
            ORDER BY total_seconds DESC
        ''').fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
