from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import requests as http_requests
import os

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///movies.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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

@app.route("/")
def index():
    status_filter = request.args.get("status")
    genre_filter = request.args.get("genre")
    
    movies = Movie.query
    if status_filter:
        movies = movies.filter_by(status=status_filter)
    if genre_filter:
        movies = movies.filter(Movie.genre.contains(genre_filter))
    movies = movies.all()

    all_genres = db.session.query(Movie.genre).filter(Movie.genre != None).all()
    genres = sorted(set(
        g.strip() 
        for row in all_genres 
        for g in row[0].split(",") 
        if g.strip()
    ))

    return render_template("index.html", movies=movies, genres=genres)

@app.route("/search")
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
def add_movie():
    title = request.form.get("title")
    overview = request.form.get("overview")
    release_date = request.form.get("release_date")
    rating = request.form.get("rating")
    poster_path = request.form.get("poster_path")
    tmdb_id = request.form.get("tmdb_id")
    status = request.form.get("status")
    genre = request.form.get("genre")

    movie = Movie(
        title=title,
        overview=overview,
        release_date=release_date,
        rating=float(rating) if rating else None,
        poster_path=poster_path,
        tmdb_id=int(tmdb_id) if tmdb_id else None,
        status=status,
        genre=genre
    )
    db.session.add(movie)
    db.session.commit()
    return redirect(url_for("index"))

    movie = Movie(
        title=title,
        overview=overview,
        release_date=release_date,
        rating=float(rating) if rating else None,
        poster_path=poster_path,
        tmdb_id=int(tmdb_id) if tmdb_id else None,
        status=status
    )
    db.session.add(movie)
    db.session.commit()
    return redirect(url_for("index"))

@app.route("/delete/<int:movie_id>")
def delete_movie(movie_id):
    movie = Movie.query.get_or_404(movie_id)
    db.session.delete(movie)
    db.session.commit()
    return redirect(url_for("index"))

@app.route("/update/<int:movie_id>", methods=["POST"])
def update_movie(movie_id):
    movie = Movie.query.get_or_404(movie_id)
    movie.status = request.form.get("status")
    my_rating = request.form.get("my_rating")
    if my_rating:
        movie.my_rating = float(my_rating)
    db.session.commit()
    return redirect(url_for("index"))

@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    movie = Movie.query.get_or_404(movie_id)
    return render_template("detail.html", movie=movie)

@app.route("/stats")
def stats():
    total = Movie.query.count()
    watched = Movie.query.filter_by(status="Watched").count()
    watching = Movie.query.filter_by(status="Watching").count()
    want = Movie.query.filter_by(status="Want to Watch").count()
    
    avg_rating = db.session.query(db.func.avg(Movie.my_rating)).scalar()
    avg_rating = round(avg_rating, 1) if avg_rating else 0

    return render_template("stats.html", 
        total=total, 
        watched=watched, 
        watching=watching, 
        want=want,
        avg_rating=avg_rating
    )
@app.route("/recommendations")
def recommendations():
    API_KEY = os.getenv("TMDB_API_KEY")
    watched = Movie.query.filter_by(status="Watched").all()
    
    seen_ids = {m.tmdb_id for m in watched}
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

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
 