"""Grafikte Publicidad: sirve el visor 3D y recibe pedidos con archivos de clientes.

Incluye el catálogo de merchandising ecológico de EcoGroove (camiseta orgánica,
mug reciclado, libreta de papel semilla, calendario) que se vende desde la
landing de EcoGroove usando este mismo backend de pedidos.
"""
import json
import os
import re
import secrets
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (Flask, Response, g, jsonify, render_template_string,
                   request, send_from_directory)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
UPLOADS.mkdir(exist_ok=True)
DB_PATH = BASE / "pedidos.db"
PRECIOS_PATH = BASE / "precios.json"
POLITICA_VERSION = "pendiente"  # cámbiala cuando publiques la política real

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")
WHATSAPP = re.sub(r"\D", "", os.environ.get("WHATSAPP", ""))
NOTIFY_TO = os.environ.get("NOTIFY_TO", "")

PRODUCTOS = {
    "tarjeta": "Tarjetas", "volante": "Volantes", "pendon": "Pendón",
    "aviso": "Avisos", "carpeta": "Carpetas", "calendario": "Calendarios",
    "libreta": "Libretas", "mug": "Mugs", "camiseta": "Camisetas",
}
# Subconjunto que la tienda "EcoGroove" vende como merchandising por unidad
# (precio fijo por unidad * cantidad, en vez de por m² o por opción impresa/no impresa).
PRODUCTOS_UNITARIOS = ("camiseta", "mug", "libreta", "calendario")
CANT_MIN, CANT_MAX = 1, 500

CAMPOS_ARCHIVO = ("diseno_frente", "diseno_reverso", "vista_frente", "vista_reverso")
EXT_OK = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB por pedido
if os.environ.get("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")


# ---------- base de datos ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def cerrar_db(_):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pedidos("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, creado TEXT, producto TEXT, "
        "precio INTEGER, estado TEXT DEFAULT 'nuevo', datos TEXT)"
    )
    con.commit()
    con.close()


init_db()


# ---------- precios (el servidor es quien manda) ----------
def cargar_precios():
    return json.loads(PRECIOS_PATH.read_text(encoding="utf-8"))


def calcular_precio(producto, opciones, medidas):
    """Devuelve el valor en pesos, o None si el producto va por cotización."""
    pr = cargar_precios()
    a = int(opciones.get("a", 0))

    if producto in ("tarjeta", "volante"):
        if a not in (0, 1):
            raise ValueError("Opción no válida")
        return pr[producto][a]

    if producto in ("pendon", "aviso"):
        w, h = medidas["w"], medidas["h"]
        if not (0.3 <= w <= 6 and 0.3 <= h <= 6):
            raise ValueError("Medidas fuera de rango (0,3 a 6 m)")
        if producto == "pendon":
            return round(w * h * pr["pendon_m2"])
        if a == 0:  # solo el aviso con bastidor tiene precio
            return round(w * h * pr["aviso_bastidor_m2"])
        return None

    if producto in PRODUCTOS_UNITARIOS:
        precio_unit = pr.get("merch", {}).get(producto)
        if precio_unit is None:
            return None  # sin precio configurado todavía -> cotizar
        cant = int(opciones.get("cant", 1))
        if not (CANT_MIN <= cant <= CANT_MAX):
            raise ValueError(f"La cantidad debe estar entre {CANT_MIN} y {CANT_MAX}")
        return round(precio_unit * cant)

    return None


# ---------- páginas y API ----------
@app.get("/")
def inicio():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/politica")
def politica():
    return send_from_directory(app.static_folder, "politica.html")


@app.get("/api/config")
def config():
    return jsonify(whatsapp=WHATSAPP)


@app.get("/api/precios")
def precios():
    return jsonify(cargar_precios())


@app.errorhandler(413)
def muy_grande(_):
    return jsonify(error="Los archivos superan el máximo de 25 MB"), 413


def notificar(pid, producto, resumen, cliente, precio):
    host = os.environ.get("SMTP_HOST")
    if not (host and NOTIFY_TO):
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Nuevo pedido #{pid}: {PRODUCTOS[producto]}"
        msg["From"] = os.environ.get("SMTP_USER", NOTIFY_TO)
        msg["To"] = NOTIFY_TO
        msg.set_content("\n".join(
            [f"Cliente: {cliente['nombre']} · {cliente['telefono']} · {cliente['correo']}",
             f"Valor: {precio if precio else 'Cotizar'}", *resumen]))
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=15) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    except Exception:
        app.logger.exception("No se pudo enviar el correo del pedido %s", pid)


@app.post("/api/pedido")
@limiter.limit("10 per hour")
def crear_pedido():
    try:
        datos = json.loads(request.form.get("datos", ""))
        producto = datos["producto"]
        if producto not in PRODUCTOS:
            raise ValueError("Producto no válido")
        c = datos.get("cliente") or {}
        cliente = {
            "nombre": str(c.get("nombre", "")).strip()[:120],
            "telefono": str(c.get("telefono", "")).strip()[:30],
            "correo": str(c.get("correo", "")).strip()[:120],
        }
        if not cliente["nombre"] or len(re.sub(r"\D", "", cliente["telefono"])) < 7:
            raise ValueError("Falta el nombre o un teléfono válido")
        if datos.get("autorizacion_datos") is not True:
            raise ValueError("Falta la autorización de tratamiento de datos")
        opciones = {str(k)[:10]: int(v) for k, v in (datos.get("opciones") or {}).items()}
        m = datos.get("medidas") or {"w": 0, "h": 0, "u": ""}
        medidas = {"w": float(m.get("w", 0)), "h": float(m.get("h", 0)), "u": str(m.get("u", ""))[:3]}
        resumen = [str(x)[:200] for x in (datos.get("resumen") or [])[:20]]
        disenos = [
            {"cara": str(d.get("cara"))[:10], "sc": float(d.get("sc", 0)),
             "x": float(d.get("x", 0)), "y": float(d.get("y", 0)),
             "archivo": str(d["archivo"])[:120] if d.get("archivo") else None}
            for d in (datos.get("disenos") or [])[:2] if isinstance(d, dict)
        ]
        precio = calcular_precio(producto, opciones, medidas)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except (TypeError, KeyError, AttributeError):
        return jsonify(error="Datos inválidos"), 400

    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    registro = {
        "producto": producto, "opciones": opciones, "medidas": medidas,
        "resumen": resumen, "disenos": disenos, "cliente": cliente,
        "autorizacion_datos": {"aceptada": True, "fecha": ahora, "politica": POLITICA_VERSION},
    }
    con = db()
    pid = con.execute(
        "INSERT INTO pedidos(creado, producto, precio, datos) VALUES (?,?,?,?)",
        (ahora, producto, precio, "{}")).lastrowid
    carpeta = UPLOADS / str(pid)
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos = []
    for campo in CAMPOS_ARCHIVO:
        f = request.files.get(campo)
        if not f or not f.filename:
            continue
        ext = ".png" if campo.startswith("vista") else Path(f.filename).suffix.lower()
        if ext not in EXT_OK:
            continue
        try:
            Image.open(f.stream).verify()  # descarta archivos que no son imágenes
            f.stream.seek(0)
        except Exception:
            continue
        f.save(carpeta / f"{campo}{ext}")  # nombre fijo: nunca el que envía el cliente
        archivos.append(f"{campo}{ext}")
    registro["archivos"] = archivos
    con.execute("UPDATE pedidos SET datos=? WHERE id=?",
                (json.dumps(registro, ensure_ascii=False), pid))
    con.commit()

    notificar(pid, producto, resumen, cliente, precio)
    wa = None
    if WHATSAPP:
        txt = f"Hola Grafikte, acabo de enviar el pedido #{pid} ({PRODUCTOS[producto]}). Te escribo para coordinar."
        wa = f"https://wa.me/{WHATSAPP}?text={quote(txt)}"
    return jsonify(id=pid, precio=precio, archivos=archivos, whatsapp=wa), 201


# ---------- panel de administración ----------
def requiere_admin(f):
    @wraps(f)
    def interno(*args, **kwargs):
        if not ADMIN_PASS:
            return "Configura ADMIN_PASS en el archivo .env", 503
        au = request.authorization
        ok = au and secrets.compare_digest((au.username or "").encode(), ADMIN_USER.encode()) \
            and secrets.compare_digest((au.password or "").encode(), ADMIN_PASS.encode())
        if not ok:
            return Response("Acceso restringido", 401,
                            {"WWW-Authenticate": 'Basic realm="Grafikte"'})
        return f(*args, **kwargs)
    return interno


ADMIN_HTML = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Pedidos · Grafikte</title>
<style>body{font:14px system-ui;margin:20px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:6px;vertical-align:top;text-align:left}th{background:#f1f1f1}</style>
<h1>Pedidos</h1>
<table><tr><th>#</th><th>Fecha (UTC)</th><th>Producto</th><th>Cliente</th><th>Valor</th><th>Detalle</th><th>Archivos</th></tr>
{% for p in pedidos %}<tr><td>{{ p.id }}</td><td>{{ p.creado[:16] }}</td><td>{{ p.producto }}</td>
<td>{{ p.cliente.nombre }}<br><a href="https://wa.me/{{ p.tel_wa }}">{{ p.cliente.telefono }}</a><br>{{ p.cliente.correo }}</td>
<td>{{ p.precio }}</td><td>{% for l in p.resumen %}{{ l }}<br>{% endfor %}</td>
<td>{% for a in p.archivos %}<a href="/admin/archivo/{{ p.id }}/{{ a }}">{{ a }}</a><br>{% endfor %}</td></tr>
{% endfor %}</table></html>"""


@app.get("/admin")
@requiere_admin
def admin():
    filas = []
    for r in db().execute("SELECT * FROM pedidos ORDER BY id DESC LIMIT 200"):
        d = json.loads(r["datos"] or "{}")
        cli = d.get("cliente", {"nombre": "", "telefono": "", "correo": ""})
        dig = re.sub(r"\D", "", cli.get("telefono", ""))
        filas.append({
            "id": r["id"], "creado": r["creado"], "producto": PRODUCTOS.get(r["producto"], r["producto"]),
            "cliente": cli, "tel_wa": "57" + dig if len(dig) == 10 else dig,  # celulares de Colombia
            "precio": ("$ " + f"{r['precio']:,}".replace(",", ".")) if r["precio"] else "Cotizar",
            "resumen": d.get("resumen", []), "archivos": d.get("archivos", []),
        })
    return render_template_string(ADMIN_HTML, pedidos=filas)


@app.get("/admin/archivo/<int:pid>/<nombre>")
@requiere_admin
def archivo(pid, nombre):
    return send_from_directory(UPLOADS / str(pid), nombre)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
