from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from urllib.parse import urlparse
from dotenv import load_dotenv
import requests as http_requests
import os
import re

load_dotenv()

app = Flask(__name__)

# On Render the RENDER env var is always set; locally it isn't.
IS_PROD = bool(os.getenv("RENDER"))

secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    if IS_PROD:
        raise RuntimeError("SECRET_KEY is not set. Add it in Render > Environment.")
    secret_key = "dev-only-secret-change-me"  # local development only
app.config["SECRET_KEY"] = secret_key
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PROD

database_url = os.getenv("DATABASE_URL", "sqlite:///movies.db")
# Render/Heroku hand out "postgres://", but SQLAlchemy needs "postgresql://"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to see your movies."
login_manager.login_message_category = "info"

VALID_STATUSES = {"Watched", "Watching", "Want to Watch"}
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")


class User(UserMixin, db.Model):
    __tablename__ = "users"  # "user" is a reserved word in Postgres

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    movies = db.relationship("Movie", backref="owner", lazy=True,
                             cascade="all, delete-orphan")


class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    overview = db.Column(db.Text)
    release_date = db.Column(db.String(20))
    rating = db.Column(db.Float)
    poster_path = db.Column(db.String(200))
    tmdb_id = db.Column(db.Integer)
    status = db.Column(db.String(50), default="Watched")
    my_rating = db.Column(db.Float)
    genre = db.Column(db.String(100))

    def __repr__(self):
        return f"<Movie {self.title}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------- helpers ----------

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_my_rating(value):
    """Return a rating between 1 and 10, or None if it's missing/invalid."""
    rating = to_float(value)
    if rating is None or not 1 <= rating <= 10:
        return None
    return rating


def is_safe_next(target):
    """Only allow redirects to paths on this site (blocks open redirects)."""
    if not target or "\\" in target or not target.startswith("/") or target.startswith("//"):
        return False
    parts = urlparse(target)
    return not parts.scheme and not parts.netloc


def get_my_movie_or_404(movie_id):
    """Fetch a movie only if it belongs to the logged-in user."""
    return Movie.query.filter_by(id=movie_id, user_id=current_user.id).first_or_404()


# ---------- auth ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        error = None
        if not USERNAME_RE.match(username):
            error = "Username must be 3-20 characters: letters, numbers or underscores."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords don't match."
        elif User.query.filter_by(username=username).first():
            error = "That username is taken."

        if error:
            flash(error, "danger")
            return render_template("register.html", username=username), 400

        is_first_user = User.query.count() == 0
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        try:
            db.session.flush()  # get user.id
            if is_first_user:
                # The very first account adopts any movies saved before accounts existed.
                Movie.query.filter(Movie.user_id.is_(None)).update({"user_id": user.id})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That username is taken.", "danger")
            return render_template("register.html", username=username), 400

        login_user(user, remember=True)
        flash("Welcome to Movie Tracker! 🎬", "success")
        return redirect(url_for("index"))

    return render_template("register.html", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=True)
            next_url = request.args.get("next")
            return redirect(next_url if is_safe_next(next_url) else url_for("index"))

        flash("Wrong username or password.", "danger")
        return render_template("login.html", username=username), 401

    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# ---------- movies (all scoped to the logged-in user) ----------

@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template("landing.html")

    status_filter = request.args.get("status")
    genre_filter = request.args.get("genre")

    movies = Movie.query.filter_by(user_id=current_user.id)
    if status_filter:
        movies = movies.filter_by(status=status_filter)
    if genre_filter:
        movies = movies.filter(Movie.genre.contains(genre_filter))
    movies = movies.all()

    all_genres = (
        db.session.query(Movie.genre)
        .filter(Movie.user_id == current_user.id, Movie.genre != None)
        .all()
    )
    genres = sorted(set(
        g.strip()
        for row in all_genres
        for g in row[0].split(",")
        if g.strip()
    ))

    return render_template("index.html", movies=movies, genres=genres)


@app.route("/search")
@login_required
def search():
    query = request.args.get("query")
    results = []
    if query:
        API_KEY = os.getenv("TMDB_API_KEY")
        url = f"https://api.themoviedb.org/3/search/movie?api_key={API_KEY}&query={query}"
        response = http_requests.get(url)
        data = response.json()
        basic_results = data.get("results", [])[:8]

        from concurrent.futures import ThreadPoolExecutor

        def fetch_details(movie):
            detail_url = f"https://api.themoviedb.org/3/movie/{movie['id']}?api_key={API_KEY}"
            detail_data = http_requests.get(detail_url).json()
            movie['genres'] = ", ".join([g['name'] for g in detail_data.get('genres', [])])
            return movie

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(fetch_details, basic_results))

    return render_template("search.html", results=results, query=query)


@app.route("/add", methods=["POST"])
@login_required
def add_movie():
    title = request.form.get("title")
    if not title:
        abort(400)

    tmdb_id = request.form.get("tmdb_id")
    tmdb_id = int(tmdb_id) if tmdb_id and tmdb_id.isdigit() else None

    # Don't let the same movie be added twice to one person's list
    if tmdb_id and Movie.query.filter_by(user_id=current_user.id, tmdb_id=tmdb_id).first():
        flash(f"“{title}” is already in your list.", "info")
        return redirect(url_for("index"))

    status = request.form.get("status")
    if status not in VALID_STATUSES:
        status = "Watched"

    movie = Movie(
        user_id=current_user.id,
        title=title[:200],
        overview=request.form.get("overview"),
        release_date=request.form.get("release_date"),
        rating=to_float(request.form.get("rating")),
        poster_path=request.form.get("poster_path"),
        tmdb_id=tmdb_id,
        status=status,
        genre=(request.form.get("genre") or "")[:100],
    )
    db.session.add(movie)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:movie_id>", methods=["POST"])
@login_required
def delete_movie(movie_id):
    movie = get_my_movie_or_404(movie_id)
    db.session.delete(movie)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/update/<int:movie_id>", methods=["POST"])
@login_required
def update_movie(movie_id):
    movie = get_my_movie_or_404(movie_id)

    status = request.form.get("status")
    if status in VALID_STATUSES:
        movie.status = status

    my_rating = request.form.get("my_rating")
    if my_rating:
        rating = parse_my_rating(my_rating)
        if rating is None:
            flash("Rating must be a number from 1 to 10.", "danger")
        else:
            movie.my_rating = rating

    db.session.commit()
    return redirect(url_for("index"))


@app.route("/movie/<int:movie_id>")
@login_required
def movie_detail(movie_id):
    movie = get_my_movie_or_404(movie_id)
    return render_template("detail.html", movie=movie)


@app.route("/stats")
@login_required
def stats():
    mine = Movie.query.filter_by(user_id=current_user.id)
    total = mine.count()
    watched = mine.filter_by(status="Watched").count()
    watching = mine.filter_by(status="Watching").count()
    want = mine.filter_by(status="Want to Watch").count()

    avg_rating = (
        db.session.query(db.func.avg(Movie.my_rating))
        .filter(Movie.user_id == current_user.id)
        .scalar()
    )
    avg_rating = round(avg_rating, 1) if avg_rating else 0

    return render_template("stats.html",
        total=total,
        watched=watched,
        watching=watching,
        want=want,
        avg_rating=avg_rating
    )


@app.route("/recommendations")
@login_required
def recommendations():
    API_KEY = os.getenv("TMDB_API_KEY")
    mine = Movie.query.filter_by(user_id=current_user.id).all()
    watched = [m for m in mine if m.status == "Watched"]


    seen_ids = {m.tmdb_id for m in mine}
    recommended = []
    seen_recommended = set()

    for movie in watched[:5]:
        if not movie.tmdb_id:
            continue
        url = f"https://api.themoviedb.org/3/movie/{movie.tmdb_id}/recommendations?api_key={API_KEY}"
        response = http_requests.get(url)
        data = response.json()
        for rec in data.get("results", [])[:3]:
            if rec["id"] not in seen_ids and rec["id"] not in seen_recommended:
                recommended.append(rec)
                seen_recommended.add(rec["id"])

    return render_template("recommendations.html", recommendations=recommended[:12])


def upgrade_schema():
    """create_all() never alters existing tables, so add the new column by hand
    for databases that were created before accounts existed."""
    try:
        inspector = inspect(db.engine)
        if "movie" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("movie")}
            if "user_id" not in columns:
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE movie ADD COLUMN user_id INTEGER"))
    except SQLAlchemyError as e:
       
        print(f"Schema upgrade skipped: {e}")


with app.app_context():
    db.create_all()
    upgrade_schema()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
