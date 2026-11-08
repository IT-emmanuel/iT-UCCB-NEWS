import os, secrets
from datetime import datetime
from xml.sax.saxutils import escape
import click, markdown, nh3
from PIL import Image
from flask import (Flask, render_template, request, redirect, url_for, session, flash,
                   abort, Response, send_from_directory)
from markupsafe import Markup
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.abspath(os.path.dirname(__file__))
DATA = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
UPLOADS = os.path.join(DATA, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(DATA, "news.db")),
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=6 * 1024 * 1024,
)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Connectez-vous pour continuer."

# ---------- Données du site ----------
KINDS = ["Actualité", "Tutoriel"]
CATEGORIES = ["Systèmes", "Réseaux", "Sécurité", "Développement", "Linux", "Actualités"]
PER_PAGE = 8
TEAM = [
    dict(name="IT-Emmanuel", slug="emmanuel", role="Technicien systèmes et réseaux junior",
         bio="Étudiant en Licence Informatique à l'UCCB. Il travaille sur Windows Server "
             "(Active Directory, DNS, DHCP, GPO), la virtualisation, les réseaux MikroTik et Cisco "
             "et la supervision."),
    dict(name="IT-Life", slug="life", role="Développeur junior",
         bio="Il conçoit et développe des sites et des applications web, et partage ses "
             "méthodes et astuces de code."),
    dict(name="IT-Ivann", slug="ivann", role="Technicien systèmes et sécurité junior",
         bio="Il administre des systèmes et applique les bonnes pratiques de sécurité : "
             "durcissement des serveurs, accès et surveillance."),
]
MEMBERS = {m["name"]: m for m in TEAM}

def photo(member):
    rel = f"img/team/{member['slug']}.jpg"
    if os.path.exists(os.path.join(app.static_folder, rel)):
        return url_for("static", filename=rel)
    return None

app.jinja_env.globals.update(photo=photo, TEAM=TEAM, MEMBERS=MEMBERS,
                             CATEGORIES=CATEGORIES, KINDS=KINDS)
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]

@app.template_filter("fr_date")
def fr_date(d):
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"

ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt"}, "*": {"class"}}

@app.template_filter("md")
def render_md(text):
    html = markdown.markdown(text, extensions=["fenced_code", "codehilite", "tables", "sane_lists"],
                             extension_configs={"codehilite": {"guess_lang": False}})
    return Markup(nh3.clean(html, attributes=ALLOWED_ATTRS))

# ---------- Modèles ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="Tutoriel")
    category = db.Column(db.String(50), nullable=False)
    cover = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author = db.relationship("User", backref="articles")

    @property
    def reading_time(self):
        return max(1, len(self.body.split()) // 200)

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

# ---------- CSRF ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]

app.jinja_env.globals["csrf_token"] = csrf_token

@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "").encode()
        if not secrets.compare_digest(sent, session.get("_csrf", "").encode()):
            abort(400)

# ---------- Images de couverture ----------
def save_cover(file):
    """Vérifie que c'est une vraie image, la réduit et l'enregistre en JPEG."""
    try:
        im = Image.open(file.stream)
        im.verify(); file.stream.seek(0)
        im = Image.open(file.stream).convert("RGB")
    except Exception:
        return None
    im.thumbnail((1200, 1200))
    name = secrets.token_hex(12) + ".jpg"
    im.save(os.path.join(UPLOADS, name), "JPEG", quality=82, optimize=True)
    return name

def drop_cover(name):
    if name:
        try: os.remove(os.path.join(UPLOADS, name))
        except OSError: pass

@app.route("/media/<name>")
def media(name):
    return send_from_directory(UPLOADS, name, max_age=86400)

app.jinja_env.globals["cover_url"] = lambda a: url_for("media", name=a.cover) if a.cover else None

# ---------- Pages publiques (aucune connexion requise) ----------
@app.route("/")
def index():
    latest = Article.query.order_by(Article.created_at.desc()).all()
    featured, rest = (latest[0] if latest else None), latest[1:]
    blocks = []
    for c in CATEGORIES:
        items = [a for a in latest if a.category == c][:3]
        if items:
            blocks.append((c, items))
    return render_template("index.html", featured=featured, blocks=blocks)

@app.route("/articles")
def articles():
    author, cat = request.args.get("auteur", ""), request.args.get("categorie", "")
    kind, q = request.args.get("type", ""), request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    query = Article.query
    if author in MEMBERS:
        query = query.join(User).filter(User.username == author)
    if cat in CATEGORIES:
        query = query.filter(Article.category == cat)
    if kind in KINDS:
        query = query.filter(Article.kind == kind)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Article.title.ilike(like), Article.summary.ilike(like),
                                    Article.body.ilike(like)))
    pg = query.order_by(Article.created_at.desc()).paginate(page=page, per_page=PER_PAGE, error_out=False)
    args = {k: v for k, v in dict(auteur=author, categorie=cat, type=kind, q=q).items() if v}
    return render_template("articles.html", pg=pg, args=args, author=author, cat=cat, kind=kind, q=q)

@app.route("/article/<int:id>")
def article(id):
    a = db.get_or_404(Article, id)
    more = (Article.query.filter(Article.author_id == a.author_id, Article.id != a.id)
            .order_by(Article.created_at.desc()).limit(3).all())
    return render_template("article.html", a=a, more=more)

@app.route("/a-propos")
def about():
    return render_template("about.html")

@app.route("/feed.xml")
def feed():
    items = Article.query.order_by(Article.created_at.desc()).limit(20).all()
    root = request.url_root.rstrip("/")
    out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>',
           "<title>IT-UCCB-NEWS</title>", f"<link>{root}/</link>",
           "<description>Actualités et tutoriels informatiques de l'UCCB</description>",
           "<language>fr</language>"]
    for a in items:
        link = f"{root}{url_for('article', id=a.id)}"
        out.append(f"<item><title>{escape(a.title)}</title><link>{link}</link><guid>{link}</guid>"
                   f"<author>{escape(a.author.username)}</author><category>{escape(a.category)}</category>"
                   f"<pubDate>{a.created_at.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
                   f"<description>{escape(a.summary)}</description></item>")
    out.append("</channel></rss>")
    return Response("".join(out), mimetype="application/rss+xml")

# ---------- Espace contributeurs ----------
@app.route("/connexion", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "")).first()
        if u and check_password_hash(u.password_hash, request.form.get("password", "")):
            login_user(u)
            return redirect(url_for("index"))
        flash("Identifiant ou mot de passe incorrect.", "error")
    return render_template("login.html")

@app.route("/deconnexion", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("index"))

def save_article(a):
    f = request.form
    a.title, a.summary = f.get("title", "").strip(), f.get("summary", "").strip()
    a.body, a.category, a.kind = f.get("body", "").strip(), f.get("category", ""), f.get("kind", "")
    if not (a.title and a.summary and a.body) or a.category not in CATEGORIES or a.kind not in KINDS:
        flash("Tous les champs sont obligatoires.", "error")
        return False
    file = request.files.get("cover")
    if file and file.filename:
        name = save_cover(file)
        if not name:
            flash("Le fichier envoyé n'est pas une image valide.", "error")
            return False
        drop_cover(a.cover); a.cover = name
    elif f.get("remove_cover"):
        drop_cover(a.cover); a.cover = None
    return True

@app.route("/nouveau", methods=["GET", "POST"])
@login_required
def new():
    a = Article(author_id=current_user.id)
    if request.method == "POST" and save_article(a):
        db.session.add(a); db.session.commit()
        flash("Article publié.", "ok")
        return redirect(url_for("article", id=a.id))
    return render_template("editor.html", a=a, mode="Publier un article")

@app.route("/modifier/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    a = db.get_or_404(Article, id)
    if a.author_id != current_user.id:
        abort(403)
    if request.method == "POST" and save_article(a):
        db.session.commit()
        flash("Article modifié.", "ok")
        return redirect(url_for("article", id=a.id))
    return render_template("editor.html", a=a, mode="Modifier l'article")

@app.route("/supprimer/<int:id>", methods=["POST"])
@login_required
def delete(id):
    a = db.get_or_404(Article, id)
    if a.author_id != current_user.id:
        abort(403)
    drop_cover(a.cover)
    db.session.delete(a); db.session.commit()
    flash("Article supprimé.", "ok")
    return redirect(url_for("articles"))

# ---------- Commandes : flask --app app <commande> ----------
@app.cli.command("init-db")
def init_db():
    db.create_all()
    click.echo("Base de données créée.")

@app.cli.command("create-user")
@click.argument("username")
@click.password_option()
def create_user(username, password):
    if username not in MEMBERS:
        raise click.ClickException("Contributeur inconnu : " + ", ".join(MEMBERS))
    u = User.query.filter_by(username=username).first() or User(username=username)
    u.password_hash = generate_password_hash(password)
    db.session.add(u); db.session.commit()
    click.echo(f"Compte {username} enregistré.")

@app.cli.command("seed-demo")
def seed_demo():
    demo = [
        ("IT-Emmanuel", "Tutoriel", "Réseaux", "VLAN : séparer les réseaux d'une entreprise",
         "Un VLAN par usage : gestion, utilisateurs, invités.",
         "Un **VLAN** découpe un réseau physique en réseaux logiques.\n\n## Configuration sur un switch Cisco\n\n```\nenable\nconfigure terminal\nvlan 10\n name Gestion\nexit\ninterface fa0/1\n switchport mode access\n switchport access vlan 10\n```\n\nEnsuite, on configure un port en `trunk` vers le routeur."),
        ("IT-Life", "Tutoriel", "Développement", "Flask : créer sa première application web",
         "Une application complète en quelques fichiers Python.",
         "Flask est un framework Python léger. Une route associe une URL à une fonction.\n\n```python\nfrom flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef index():\n    return 'Bonjour'\n```\n\nLes templates Jinja2 génèrent les pages HTML."),
        ("IT-Ivann", "Actualité", "Sécurité", "Pourquoi activer l'authentification à deux facteurs",
         "Un mot de passe seul ne suffit plus.",
         "Avec un second facteur, le vol d'un mot de passe ne suffit plus pour accéder au compte.\n\n- Messagerie\n- Comptes d'administration\n- Gestionnaire de mots de passe\n\nActivez-le d'abord sur ces trois accès."),
    ]
    for name, kind, cat, title, summary, body in demo:
        u = User.query.filter_by(username=name).first()
        if u:
            db.session.add(Article(author_id=u.id, kind=kind, category=cat, title=title,
                                   summary=summary, body=body))
    db.session.commit()
    click.echo("Articles de démonstration ajoutés.")

if __name__ == "__main__":
    app.run(debug=True)
