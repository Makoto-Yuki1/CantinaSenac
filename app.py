from __future__ import annotations
import json
import os
import secrets
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Table, Column, ForeignKey
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

instance_dir = Path(app.root_path) / "instance"
instance_dir.mkdir(exist_ok=True)
database_path = (instance_dir / "database.db").as_posix()
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database_path}"

DATA_FILE = Path(app.root_path) / "app.json"  # usado somente para importar os dados antigos

db = SQLAlchemy(app)

produto_ingredientes = Table(
    "produto_ingredientes",
    db.metadata,
    Column("produto_id", ForeignKey("produtos.id"), primary_key=True),
    Column("ingrediente_id", ForeignKey("ingredientes.id"), primary_key=True),
)
produto_restricoes = Table(
    "produto_restricoes",
    db.metadata,
    Column("produto_id", ForeignKey("produtos.id"), primary_key=True),
    Column("restricao_id", ForeignKey("restricoes.id"), primary_key=True),
)


class User(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="aluno")
    restricoes = db.Column(db.JSON, nullable=False, default=list)
    preferencias = db.Column(db.JSON, nullable=False, default=list)
    orders = relationship("Order", back_populates="user")


class Product(db.Model):
    __tablename__ = "produtos"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    descricao = db.Column(db.Text, nullable=False, default="")
    tipo = db.Column(db.String(80), nullable=False, default="")
    preco = db.Column(db.Float, nullable=False, default=0)
    ingredientes_rel = relationship("Ingredient", secondary=produto_ingredientes)
    restricoes_rel = relationship("Restriction", secondary=produto_restricoes)

    @property
    def ingredientes(self):
        return [ingrediente.nome for ingrediente in self.ingredientes_rel]

    @property
    def restricoes(self):
        return [restricao.nome for restricao in self.restricoes_rel]

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "descricao": self.descricao,
            "ingredientes": self.ingredientes,
            "restricoes": self.restricoes,
            "tipo": self.tipo,
            "preco": self.preco,
        }


class Ingredient(db.Model):
    __tablename__ = "ingredientes"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), unique=True, nullable=False)


class Restriction(db.Model):
    __tablename__ = "restricoes"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), unique=True, nullable=False)


class Order(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, ForeignKey("usuarios.id"), nullable=False)
    nome_aluno = db.Column(db.String(160), nullable=False, default="")
    turma = db.Column(db.String(80), nullable=False, default="")
    observacao = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(30), nullable=False, default="Pendente")
    user = relationship("User", back_populates="orders")
    itens = relationship("OrderItem", back_populates="pedido", cascade="all, delete-orphan")

    @property
    def username(self):
        return self.user.username

    @property
    def total(self):
        return sum(item.quantidade * item.produto.preco for item in self.itens)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "nome_aluno": self.nome_aluno,
            "turma": self.turma,
            "itens": [item.to_dict() for item in self.itens],
            "observacao": self.observacao,
            "status": self.status,
        }


class OrderItem(db.Model):
    __tablename__ = "itens_pedido"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, ForeignKey("pedidos.id"), nullable=False)
    product_id = db.Column(db.Integer, ForeignKey("produtos.id"), nullable=False)
    product_name = db.Column(db.String(160), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False, default=1)
    pedido = relationship("Order", back_populates="itens")
    produto = relationship("Product")

    @property
    def nome(self):
        return self.product_name

    def to_dict(self):
        return {
            "id": self.product_id,
            "nome": self.product_name,
            "quantidade": self.quantidade,
        }


class Migration(db.Model):
    __tablename__ = "migracoes"

    chave = db.Column(db.String(80), primary_key=True)


_db_initialized = False


def configure_admin_account():
    """Cria/atualiza o administrador usando credenciais fornecidas no ambiente."""
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return

    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username)
        db.session.add(user)
    user.role = "admin"
    if not user.password_hash or not check_password_hash(user.password_hash, password):
        user.password_hash = generate_password_hash(password)
    db.session.commit()


def initialize_database():
    """Cria tabelas e importa app.json uma única vez, sem apagar o arquivo original."""
    global _db_initialized
    if _db_initialized:
        return

    db.create_all()
    configure_admin_account()
    if db.session.get(Migration, "app_json_para_sql_v1"):
        _db_initialized = True
        return

    if DATA_FILE.exists():
        with DATA_FILE.open("r", encoding="utf-8") as arquivo:
            legado = json.load(arquivo)

        for registro in legado.get("menu", []):
            product = db.session.get(Product, registro["id"])
            if product is None:
                product = Product(
                    id=registro["id"],
                    nome=registro["nome"],
                    descricao=registro.get("descricao", ""),
                    tipo=registro.get("tipo", ""),
                    preco=float(registro.get("preco", 0)),
                )
                product.ingredientes_rel = [
                    Ingredient.query.filter_by(nome=nome).first()
                    or Ingredient(nome=nome)
                    for nome in registro.get("ingredientes", [])
                ]
                product.restricoes_rel = [
                    Restriction.query.filter_by(nome=nome).first()
                    or Restriction(nome=nome)
                    for nome in registro.get("restricoes", [])
                ]
                db.session.add(product)

        for registro in legado.get("users", []):
            username = registro.get("username", "").strip()
            if not username:
                continue
            user = User.query.filter_by(username=username).first()
            if user is None:
                password = registro.get("password")
                user = User(
                    username=username,
                    password_hash=generate_password_hash(password) if password else None,
                    restricoes=registro.get("restricoes", []),
                    preferencias=registro.get("preferencias", []),
                )
                db.session.add(user)

        db.session.flush()
        for registro in legado.get("orders", []):
            if db.session.get(Order, registro.get("id")) is not None:
                continue
            username = registro.get("username", "Aluno")
            user = User.query.filter_by(username=username).first()
            if user is None:
                user = User(username=username)
                db.session.add(user)
                db.session.flush()

            itens_pedido = []
            for item in registro.get("itens", []):
                product = db.session.get(Product, item.get("id"))
                if product is not None:
                    itens_pedido.append(
                        OrderItem(
                            produto=product,
                            product_name=item.get("nome", product.nome),
                            quantidade=max(1, int(item.get("quantidade", 1))),
                        )
                    )
            if itens_pedido:
                db.session.add(
                    Order(
                        id=registro.get("id"),
                        user=user,
                        nome_aluno=registro.get("nome_aluno") or username,
                        turma=registro.get("turma", ""),
                        observacao=registro.get("observacao", ""),
                        status=registro.get("status", "Pendente"),
                        itens=itens_pedido,
                    )
                )

    db.session.add(Migration(chave="app_json_para_sql_v1"))
    db.session.commit()
    _db_initialized = True


@app.before_request
def ensure_database():
    initialize_database()


def current_user():
    return session.get("username")


def filter_menu(menu, restricoes, preferencia):
    resultado = []
    for item in menu:
        tags = {tag.lower() for tag in item.restricoes}
        if restricoes and not all(restricao.lower() in tags for restricao in restricoes):
            continue
        resultado.append(item)

    if preferencia:
        termo = preferencia.lower()
        resultado.sort(
            key=lambda item: 0
            if termo in item.nome.lower()
            or termo in item.descricao.lower()
            or any(termo in ingrediente.lower() for ingrediente in item.ingredientes)
            else 1
        )
    return resultado


def get_user_orders(username):
    return (
        Order.query.join(User)
        .filter(User.username == username)
        .order_by(Order.id.desc())
        .all()
    )


def user_to_dict(user):
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "restricoes": user.restricoes,
        "preferencias": user.preferencias,
    }


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_csrf_token():
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(submitted and expected and secrets.compare_digest(submitted, expected))


def safe_local_url(value):
    parsed = urlparse(value or "")
    if value.startswith("/") and not value.startswith("//") and not parsed.netloc:
        return value
    return None


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        admin_id = session.get("admin_user_id")
        admin = db.session.get(User, admin_id) if admin_id else None
        if admin is None or admin.role != "admin":
            session.pop("admin_user_id", None)
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)

    return wrapped


@app.route("/")
def home():
    return redirect(url_for("login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    mensagem = ""
    if request.method == "POST":
        if not valid_csrf_token():
            return "Token de segurança inválido. Atualize a página e tente novamente.", 400

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, role="admin").first()
        if user and user.password_hash and check_password_hash(user.password_hash, password):
            session.clear()
            session["admin_user_id"] = user.id
            return redirect(url_for("admin_dashboard"))
        mensagem = "Usuário ou senha incorretos."

    admin_configurado = User.query.filter_by(role="admin").first() is not None
    return render_template(
        "admin_login.html",
        mensagem=mensagem,
        admin_configurado=admin_configurado,
        csrf_token=csrf_token(),
    )


@app.route("/admin", methods=["GET"])
@admin_required
def admin_dashboard():
    admin = db.session.get(User, session["admin_user_id"])
    pedidos = Order.query.order_by(Order.id.desc()).all()
    return render_template(
        "admin.html",
        admin_username=admin.username,
        pedidos=pedidos,
        total_pedidos=Order.query.count(),
        pendentes=Order.query.filter_by(status="Pendente").count(),
        em_preparo=Order.query.filter_by(status="Em preparo").count(),
        concluidos=Order.query.filter_by(status="Concluído").count(),
        csrf_token=csrf_token(),
    )


@app.route("/admin/pedidos/<int:order_id>/status", methods=["POST"])
@admin_required
def admin_atualizar_status(order_id):
    if not valid_csrf_token():
        return "Token de segurança inválido. Atualize a página e tente novamente.", 400

    pedido = db.get_or_404(Order, order_id)
    proximos_status = {"Pendente": "Em preparo", "Em preparo": "Concluído"}
    novo_status = proximos_status.get(pedido.status)
    if novo_status:
        pedido.status = novo_status
        db.session.commit()
        flash(f"Pedido #{pedido.id} atualizado para {novo_status}.", "success")
    else:
        flash("Este pedido já foi concluído.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    if not valid_csrf_token():
        return "Token de segurança inválido. Atualize a página e tente novamente.", 400
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/index", methods=["GET", "POST"])
def login():
    mensagem = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            mensagem = "Informe usuário e senha."
        else:
            user = User.query.filter_by(username=username).first()
            if user:
                if user.role == "admin":
                    mensagem = "Use o acesso administrativo para entrar nesta conta."
                elif not user.password_hash or not check_password_hash(user.password_hash, password):
                    mensagem = "Usuário ou senha incorretos."
                else:
                    session["username"] = user.username
                    session["user_id"] = user.id
                    return redirect(url_for("cardapio"))
            else:
                user = User(username=username, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                session["username"] = user.username
                session["user_id"] = user.id
                return redirect(url_for("cardapio"))
    return render_template(
        "index.html", login_message=mensagem, username=current_user(), menu=[],
        orders=[], restricoes=[], preferencia="",
    )


@app.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "POST":
        session.clear()
        return redirect(url_for("login"))
    return render_template("logout.html", username=current_user())


@app.route("/cardapio")
def cardapio():
    restricoes = request.args.getlist("restricao")
    preferencia = request.args.get("preferencia", "")
    menu = filter_menu(Product.query.order_by(Product.id).all(), restricoes, preferencia)
    pedidos = get_user_orders(current_user()) if current_user() else []
    if request.args.get("format") == "json":
        return jsonify([item.to_dict() for item in menu])
    return render_template(
        "cardapio.html", username=current_user(), menu=menu, orders=pedidos,
        restricoes=restricoes, preferencia=preferencia, login_message="", edit_order=None,
    )


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    mensagem = ""
    next_url = request.values.get("next", "")
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        turma = request.form.get("turma", "").strip()
        if not nome:
            mensagem = "Informe o nome do aluno."
        else:
            user = User.query.filter_by(username=nome).first()
            if user and (user.password_hash or user.role == "admin"):
                mensagem = "Esta conta usa senha. Entre pela tela de login."
            else:
                if user is None:
                    user = User(username=nome)
                    db.session.add(user)
                    db.session.commit()
                session["username"] = user.username
                session["user_id"] = user.id
                session["turma"] = turma
                return redirect(safe_local_url(next_url) or url_for("cardapio"))
    return render_template(
        "login_student.html", message=mensagem, username=current_user(), next_url=next_url
    )


@app.route("/pedido", methods=["GET"])
def pedido_page():
    if not current_user():
        return redirect(url_for("entrar"))
    return render_template(
        "pedido.html", username=current_user(), turma=session.get("turma"),
        menu=Product.query.order_by(Product.id).all(),
        selected_id=request.args.get("item_id", type=int),
    )


@app.route("/pedido", methods=["POST"])
def criar_pedido():
    if not current_user():
        return redirect(url_for("entrar"))
    user = User.query.filter_by(username=current_user()).first()
    item = db.session.get(Product, request.form.get("item_id", type=int))
    try:
        quantidade = int(request.form.get("quantidade", 1))
    except (TypeError, ValueError):
        quantidade = 1
    if user is None or item is None or quantidade < 1:
        return redirect(url_for("pedido_page"))

    turma = request.form.get("turma", "").strip() or session.get("turma", "")
    pedido = Order(
        user=user,
        nome_aluno=request.form.get("nome", "").strip() or user.username,
        turma=turma,
        observacao=request.form.get("observacao", "").strip(),
        itens=[OrderItem(produto=item, product_name=item.nome, quantidade=quantidade)],
    )
    db.session.add(pedido)
    db.session.commit()
    return redirect(url_for("confirmacao"))


@app.route("/pedidos")
def meus_pedidos():
    if not current_user():
        return redirect(url_for("entrar", next=url_for("meus_pedidos")))
    return render_template(
        "históricodepedidos.html", username=current_user(), orders=get_user_orders(current_user())
    )


@app.route("/confirmacao")
def confirmacao():
    return render_template("confirmacao.html", username=current_user())


@app.route("/pedido/<int:order_id>/editar", methods=["GET", "POST"])
def editar_pedido(order_id):
    if not current_user():
        return redirect(url_for("entrar"))
    pedido = (
        Order.query.join(User)
        .filter(Order.id == order_id, User.username == current_user())
        .first_or_404()
    )
    if request.method == "POST":
        try:
            quantidade = int(request.form.get("quantidade", 1))
        except (TypeError, ValueError):
            quantidade = 1
        if quantidade < 1:
            db.session.delete(pedido)
        else:
            item_id = request.form.get("item_id", type=int)
            produto = db.session.get(Product, item_id) if item_id else None
            if produto and pedido.itens:
                pedido.itens[0].produto = produto
                pedido.itens[0].product_name = produto.nome
            if pedido.itens:
                pedido.itens[0].quantidade = quantidade
            pedido.nome_aluno = request.form.get("nome", pedido.nome_aluno).strip()
            pedido.turma = request.form.get("turma", pedido.turma).strip()
            pedido.observacao = request.form.get("observacao", "").strip()
        db.session.commit()
        return redirect(url_for("meus_pedidos"))
    return render_template(
        "editarpedido.html", username=current_user(), pedido=pedido,
        menu=Product.query.order_by(Product.id).all(),
    )


@app.route("/api/cardapio")
def api_cardapio():
    menu = filter_menu(
        Product.query.order_by(Product.id).all(),
        request.args.getlist("restricao"),
        request.args.get("preferencia", ""),
    )
    return jsonify([item.to_dict() for item in menu])


@app.route("/api/pedidos")
def api_pedidos():
    if not current_user():
        return jsonify({"erro": "Usuário não autenticado."}), 401
    return jsonify([pedido.to_dict() for pedido in get_user_orders(current_user())])


@app.route("/api/usuario")
def api_usuario():
    if not current_user():
        return jsonify({"logado": False})
    user = User.query.filter_by(username=current_user()).first()
    return jsonify({"logado": bool(user), "usuario": user_to_dict(user) if user else None})


@app.errorhandler(404)
def pagina_nao_encontrada(error):
    template = Path(app.template_folder) / "404.html"
    if template.exists():
        return render_template("404.html"), 404
    return "Página não encontrada", 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
