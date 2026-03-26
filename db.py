import os
import sqlite3
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "melodra.db")

_local = threading.local()


def get_db():
    """Get a thread-local SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL UNIQUE,
            password   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS projects (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            title      TEXT NOT NULL,
            type       TEXT NOT NULL DEFAULT 'solo',
            job_id     TEXT NOT NULL,
            parts_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS plays (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            played_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Add is_favorite column to projects if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE projects ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()


def create_user(name, email, password_hash):
    """Insert a new user. Returns the user id. Raises on duplicate email."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
        (name, email, password_hash),
    )
    db.commit()
    return cur.lastrowid


def get_user_by_email(email):
    """Return user row or None."""
    return get_db().execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()


def get_user_by_id(user_id):
    """Return user row or None."""
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def create_project(user_id, title, proj_type, job_id, parts_json=None):
    """Insert a project row. Returns the project id."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO projects (user_id, title, type, job_id, parts_json) VALUES (?, ?, ?, ?, ?)",
        (user_id, title, proj_type, job_id, parts_json),
    )
    db.commit()
    return cur.lastrowid


def get_user_projects(user_id):
    """Return all projects for a user, newest first."""
    return get_db().execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()


def get_project(project_id, user_id):
    """Return a single project scoped to the user, or None."""
    return get_db().execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()


def get_user_projects_enriched(user_id):
    """Return all projects for a user with play_count and last_played, newest first."""
    return get_db().execute("""
        SELECT p.*,
               COALESCE(s.play_count, 0) AS play_count,
               s.last_played
        FROM projects p
        LEFT JOIN (
            SELECT project_id,
                   COUNT(*) AS play_count,
                   MAX(played_at) AS last_played
            FROM plays
            GROUP BY project_id
        ) s ON s.project_id = p.id
        WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    """, (user_id,)).fetchall()


def record_play(user_id, project_id):
    """Record a play event."""
    db = get_db()
    db.execute(
        "INSERT INTO plays (user_id, project_id) VALUES (?, ?)",
        (user_id, project_id),
    )
    db.commit()


def get_recent_plays(user_id, limit=10):
    """Return recently played projects (most recent play per project)."""
    return get_db().execute("""
        SELECT p.*, MAX(pl.played_at) AS played_at
        FROM plays pl
        JOIN projects p ON p.id = pl.project_id
        WHERE pl.user_id = ?
        GROUP BY pl.project_id
        ORDER BY MAX(pl.played_at) DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()


def toggle_favorite(project_id, user_id):
    """Toggle is_favorite for a project. Returns new value (0 or 1)."""
    db = get_db()
    row = db.execute(
        "SELECT is_favorite FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    if not row:
        return None
    new_val = 0 if row["is_favorite"] else 1
    db.execute(
        "UPDATE projects SET is_favorite = ? WHERE id = ? AND user_id = ?",
        (new_val, project_id, user_id),
    )
    db.commit()
    return new_val


def delete_project(project_id, user_id):
    """Delete a project and its plays. Returns True if deleted."""
    db = get_db()
    cur = db.execute(
        "DELETE FROM projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    )
    db.commit()
    return cur.rowcount > 0


def rename_project(project_id, user_id, new_title):
    """Rename a project. Returns True if updated."""
    db = get_db()
    cur = db.execute(
        "UPDATE projects SET title = ? WHERE id = ? AND user_id = ?",
        (new_title, project_id, user_id),
    )
    db.commit()
    return cur.rowcount > 0
