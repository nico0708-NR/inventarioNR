import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# ─────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario  TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol      TEXT DEFAULT 'viewer'
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS productos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL,
            sku         TEXT UNIQUE,
            categoria   TEXT,
            cantidad    INTEGER DEFAULT 0,
            precio      REAL DEFAULT 0.0,
            costo       REAL DEFAULT 0.0,
            ubicacion   TEXT,
            proveedor   TEXT,
            stock_min   INTEGER DEFAULT 5,
            activo      INTEGER DEFAULT 1,
            creado_en   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS movimientos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER,
            tipo        TEXT,
            cantidad    INTEGER,
            nota        TEXT,
            usuario     TEXT,
            fecha       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(producto_id) REFERENCES productos(id)
        )""")
        # Seed admin user
        existing = c.execute("SELECT id FROM usuarios WHERE usuario='admin'").fetchone()
        if not existing:
            c.execute(
                "INSERT INTO usuarios (usuario, password, rol) VALUES (?,?,?)",
                ("admin", generate_password_hash("Admin2024!"), "admin")
            )
        conn.commit()

init_db()

# ─────────────────────────────────────────
#  AUTH DECORATOR
# ─────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def validate_product_form(form):
    errors = []
    nombre   = form.get("nombre", "").strip()
    cantidad = form.get("cantidad", "")
    precio   = form.get("precio", "")
    costo    = form.get("costo", "0")

    if not nombre:
        errors.append("El nombre es obligatorio.")
    try:
        cantidad = int(cantidad)
        if cantidad < 0:
            raise ValueError
    except ValueError:
        errors.append("Cantidad debe ser un entero no negativo.")
        cantidad = 0
    try:
        precio = float(precio)
        if precio < 0:
            raise ValueError
    except ValueError:
        errors.append("Precio debe ser un número positivo.")
        precio = 0.0
    try:
        costo = float(costo) if costo else 0.0
        if costo < 0:
            raise ValueError
    except ValueError:
        errors.append("Costo debe ser un número positivo.")
        costo = 0.0

    return errors, nombre, cantidad, precio, costo

# ─────────────────────────────────────────
#  AUTH ROUTES
# ─────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect("/")
    error = None
    if request.method == "POST":
        u = request.form.get("usuario", "").strip()
        p = request.form.get("password", "")
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM usuarios WHERE usuario=?", (u,)
            ).fetchone()
        if row and check_password_hash(row["password"], p):
            session["user"] = row["usuario"]
            session["rol"]  = row["rol"]
            return redirect("/")
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ─────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    with get_db() as conn:
        productos = conn.execute(
            "SELECT * FROM productos WHERE activo=1"
        ).fetchall()
        movimientos = conn.execute(
            """SELECT m.*, p.nombre as prod_nombre
               FROM movimientos m
               JOIN productos p ON m.producto_id = p.id
               ORDER BY m.fecha DESC LIMIT 10"""
        ).fetchall()

    total_productos = len(productos)
    valor_inventario = sum((r["cantidad"] or 0) * (r["precio"] or 0) for r in productos)
    costo_inventario = sum((r["cantidad"] or 0) * (r["costo"] or 0) for r in productos)
    bajo_stock = [r for r in productos if (r["cantidad"] or 0) <= (r["stock_min"] or 5)]

    categorias = {}
    for r in productos:
        cat = r["categoria"] or "Sin categoría"
        categorias[cat] = categorias.get(cat, 0) + 1

    return render_template(
        "dashboard.html",
        total_productos=total_productos,
        valor_inventario=valor_inventario,
        costo_inventario=costo_inventario,
        bajo_stock=bajo_stock,
        categorias=categorias,
        movimientos=movimientos,
        productos=productos
    )

# ─────────────────────────────────────────
#  API JSON
# ─────────────────────────────────────────
@app.route("/api/productos")
@login_required
def api_productos():
    q = request.args.get("q", "").strip()
    cat = request.args.get("categoria", "").strip()
    with get_db() as conn:
        sql = "SELECT * FROM productos WHERE activo=1"
        params = []
        if q:
            sql += " AND (nombre LIKE ? OR sku LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if cat:
            sql += " AND categoria=?"
            params.append(cat)
        sql += " ORDER BY nombre"
        rows = conn.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/stats")
@login_required
def api_stats():
    with get_db() as conn:
        productos = conn.execute("SELECT * FROM productos WHERE activo=1").fetchall()
    total = len(productos)
    valor = sum((r["cantidad"] or 0) * (r["precio"] or 0) for r in productos)
    bajo_stock = sum(1 for r in productos if (r["cantidad"] or 0) <= (r["stock_min"] or 5))
    categorias = {}
    for r in productos:
        cat = r["categoria"] or "Sin categoría"
        categorias[cat] = categorias.get(cat, 0) + (r["cantidad"] or 0)
    return jsonify({
        "total": total,
        "valor": round(valor, 2),
        "bajo_stock": bajo_stock,
        "categorias": categorias
    })

# ─────────────────────────────────────────
#  CRUD PRODUCTOS
# ─────────────────────────────────────────
@app.route("/agregar", methods=["POST"])
@login_required
def agregar():
    errors, nombre, cantidad, precio, costo = validate_product_form(request.form)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect("/")

    sku       = request.form.get("sku", "").strip() or None
    categoria = request.form.get("categoria", "").strip()
    ubicacion = request.form.get("ubicacion", "").strip()
    proveedor = request.form.get("proveedor", "").strip()
    stock_min = int(request.form.get("stock_min", 5) or 5)

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO productos
               (nombre,sku,categoria,cantidad,precio,costo,ubicacion,proveedor,stock_min)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (nombre, sku, categoria, cantidad, precio, costo, ubicacion, proveedor, stock_min)
        )
        prod_id = cur.lastrowid
        conn.execute(
            "INSERT INTO movimientos (producto_id,tipo,cantidad,nota,usuario) VALUES (?,?,?,?,?)",
            (prod_id, "entrada", cantidad, "Producto creado", session["user"])
        )
        conn.commit()
    return redirect("/")

@app.route("/editar/<int:pid>", methods=["POST"])
@login_required
def editar(pid):
    errors, nombre, cantidad, precio, costo = validate_product_form(request.form)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect("/")

    sku       = request.form.get("sku", "").strip() or None
    categoria = request.form.get("categoria", "").strip()
    ubicacion = request.form.get("ubicacion", "").strip()
    proveedor = request.form.get("proveedor", "").strip()
    stock_min = int(request.form.get("stock_min", 5) or 5)

    with get_db() as conn:
        old = conn.execute("SELECT cantidad FROM productos WHERE id=?", (pid,)).fetchone()
        conn.execute(
            """UPDATE productos SET nombre=?,sku=?,categoria=?,cantidad=?,precio=?,
               costo=?,ubicacion=?,proveedor=?,stock_min=? WHERE id=?""",
            (nombre, sku, categoria, cantidad, precio, costo, ubicacion, proveedor, stock_min, pid)
        )
        if old and old["cantidad"] != cantidad:
            diff = cantidad - old["cantidad"]
            tipo = "entrada" if diff > 0 else "salida"
            conn.execute(
                "INSERT INTO movimientos (producto_id,tipo,cantidad,nota,usuario) VALUES (?,?,?,?,?)",
                (pid, tipo, abs(diff), "Edición manual", session["user"])
            )
        conn.commit()
    return redirect("/")

@app.route("/eliminar/<int:pid>", methods=["POST"])
@login_required
def eliminar(pid):
    with get_db() as conn:
        conn.execute("UPDATE productos SET activo=0 WHERE id=?", (pid,))
        conn.commit()
    return redirect("/")

@app.route("/ajuste/<int:pid>", methods=["POST"])
@login_required
def ajuste_stock(pid):
    tipo     = request.form.get("tipo")
    cantidad = request.form.get("cantidad", "0")
    nota     = request.form.get("nota", "").strip()

    if tipo not in ("entrada", "salida"):
        return redirect("/")
    try:
        cantidad = int(cantidad)
        if cantidad <= 0:
            raise ValueError
    except ValueError:
        return redirect("/")

    with get_db() as conn:
        prod = conn.execute("SELECT cantidad FROM productos WHERE id=?", (pid,)).fetchone()
        if not prod:
            return redirect("/")
        nueva = prod["cantidad"] + cantidad if tipo == "entrada" else max(0, prod["cantidad"] - cantidad)
        conn.execute("UPDATE productos SET cantidad=? WHERE id=?", (nueva, pid))
        conn.execute(
            "INSERT INTO movimientos (producto_id,tipo,cantidad,nota,usuario) VALUES (?,?,?,?,?)",
            (pid, tipo, cantidad, nota or "Ajuste manual", session["user"])
        )
        conn.commit()
    return redirect("/")

# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)