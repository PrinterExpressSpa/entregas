from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
import mysql.connector
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from PIL import Image
from werkzeug.utils import secure_filename
import logging

# Configurar logging
logging.basicConfig(filename='entregas.log', level=logging.INFO)

chile_tz = timezone(timedelta(hours=-4))
load_dotenv()

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'printerexpress_key'
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

SMTP_CONFIG = {
    "host": os.getenv("SMTP_HOST"),
    "port": int(os.getenv("SMTP_PORT")),
    "user": os.getenv("SMTP_USER"),
    "password": os.getenv("SMTP_PASSWORD")
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def obtener_datos_pedido(pedido_id):
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM impresiones WHERE id = %s", (pedido_id,))
    pedido = cursor.fetchone()
    conn.close()
    return pedido

def enviar_correo(destinatario, asunto, cuerpo, imagen_path):
    msg = EmailMessage()
    msg["From"] = SMTP_CONFIG["user"]
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.set_content(cuerpo)

    with open(imagen_path, "rb") as f:
        img_data = f.read()
        msg.add_attachment(img_data, maintype="image", subtype="jpeg", filename=os.path.basename(imagen_path))

    server = smtplib.SMTP(SMTP_CONFIG["host"], SMTP_CONFIG["port"])
    server.starttls()
    server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
    server.send_message(msg)
    server.quit()

def registrar_entrega(pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario="", email_enviado=1, error_envio=""):
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    query = """
        INSERT INTO entregas (pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    valores = (pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
    cursor.execute(query, valores)
    conn.commit()
    conn.close()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        pedido_id = request.form.get("pedido_id")
        entregado_por = request.form.get("entregado_por") or "PrinterExpress"
        comentario = request.form.get("comentario") or "Entregado"

        file = request.files.get("imagen") or request.files.get("imagen_galeria")
        if not file or file.filename == "":
            flash("Debe adjuntar una imagen de la entrega.", "error")
            return redirect(request.url)

        if not pedido_id or not entregado_por:
            flash("Todos los campos son obligatorios.", "error")
            return redirect(request.url)

        pedido = obtener_datos_pedido(pedido_id)
        if not pedido:
            flash(f"❌ El pedido #{pedido_id} no existe en la base de datos.", "error")
            return redirect(request.url)

        if not allowed_file(file.filename):
            flash("Solo se permiten imágenes en formato JPG, JPEG o PNG.", "error")
            return redirect(request.url)

        momento_foto = datetime.now(chile_tz)
        fecha_entrega = momento_foto.strftime('%d/%m/%Y %H:%M:%S')

        filename = secure_filename(f"entrega_{pedido_id}_{momento_foto.strftime('%Y%m%d%H%M%S')}.jpg")
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        file.save(image_path)

        try:
            img = Image.open(image_path)
            img = img.convert('RGB')
            img.thumbnail((1024, 1024))
            img.save(image_path, format='JPEG', quality=50, dpi=(72, 72), optimize=True)
        except Exception as e:
            flash(f"⚠️ No se pudo procesar la imagen: {e}", "error")
            return redirect(request.url)

        correo = pedido["email"]
        asunto = f"Pedido {pedido_id} Entregado"
        cuerpo = (
            f"Hola {pedido['nombre']},\n\n"
            f"Tu pedido número {pedido_id} ha sido entregado con éxito el día {momento_foto}.\n\n"
            f"Adjuntamos una imagen como respaldo.\n\n"
            f"Gracias por preferirnos.\n\n"
            f"Equipo de Repartos\nPrinterExpress Spa"
        )

        try:
            enviar_correo(correo, asunto, cuerpo, image_path)
            registrar_entrega(pedido_id, fecha_entrega, image_path, entregado_por, comentario, 1, "")
            flash("✅ Correo enviado y entrega registrada correctamente.", "success")
        except Exception as e:
            error_mensaje = f"{type(e).__name__}: {str(e)}"
            registrar_entrega(pedido_id, fecha_entrega, image_path, entregado_por, comentario, 0, error_mensaje)
            flash(f"⚠️ Error al enviar el correo, pero entrega registrada. Detalle: {error_mensaje}", "error")

        return redirect(url_for("index"))

    return render_template("formulario.html")

@app.route("/datos_cliente/<int:pedido_id>")
def datos_cliente(pedido_id):
    pedido = obtener_datos_pedido(pedido_id)
    if not pedido:
        return jsonify({"error": "Pedido no encontrado"}), 404

    comuna_nombre = "-"
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT nombre FROM comunas WHERE id = %s", (pedido["comuna"],))
        result = cursor.fetchone()
        if result:
            comuna_nombre = result[0]
        conn.close()
    except:
        pass

    return jsonify({
        "nombre": pedido["nombre"],
        "direccion": pedido["direccion"],
        "comuna": comuna_nombre
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
