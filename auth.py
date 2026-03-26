import os
import datetime
from functools import wraps

import jwt
import bcrypt
from flask import Blueprint, request, redirect, url_for, render_template, g, make_response

from db import get_user_by_email, get_user_by_id, create_user

auth_bp = Blueprint("auth_bp", __name__)

JWT_SECRET = os.environ.get("MELODRA_SECRET", "melodra-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
TOKEN_COOKIE = "melodra_token"
TOKEN_EXPIRY_DAYS = 7


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=TOKEN_EXPIRY_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_current_user_from_cookie():
    """Read the JWT cookie and return the user row, or None."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return None
    payload = _decode_token(token)
    if not payload:
        return None
    return get_user_by_id(payload["user_id"])


# ── Decorator ─────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(TOKEN_COOKIE)
        if not token:
            return redirect(url_for("auth_bp.login"))
        payload = _decode_token(token)
        if not payload:
            resp = redirect(url_for("auth_bp.login"))
            resp.delete_cookie(TOKEN_COOKIE)
            return resp
        user = get_user_by_id(payload["user_id"])
        if not user:
            resp = redirect(url_for("auth_bp.login"))
            resp.delete_cookie(TOKEN_COOKIE)
            return resp
        g.user = user
        return f(*args, **kwargs)
    return decorated


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET"])
def login():
    # If already logged in, go to dashboard
    user = get_current_user_from_cookie()
    if user:
        return redirect("/dashboard")
    return render_template("login.html")


@auth_bp.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return {"error": "Email and password are required."}, 400

    user = get_user_by_email(email)
    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return {"error": "Invalid email or password."}, 401

    token = _create_token(user["id"])
    resp = make_response({"ok": True, "redirect": "/dashboard"})
    resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="Lax", max_age=TOKEN_EXPIRY_DAYS * 86400)
    return resp


@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not name or not email or not password:
        return {"error": "Name, email, and password are required."}, 400
    if len(password) < 8:
        return {"error": "Password must be at least 8 characters."}, 400

    # Check duplicate
    if get_user_by_email(email):
        return {"error": "An account with that email already exists."}, 409

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = create_user(name, email, hashed)

    token = _create_token(user_id)
    resp = make_response({"ok": True, "redirect": "/dashboard"})
    resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="Lax", max_age=TOKEN_EXPIRY_DAYS * 86400)
    return resp


@auth_bp.route("/logout")
def logout():
    resp = redirect("/")
    resp.delete_cookie(TOKEN_COOKIE)
    return resp
