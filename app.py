from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
import mysql.connector
import smtplib
from email.message import EmailMessage
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

UPLOAD_FOLDER = 'static/uploads'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'printerexpress_key'

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

def registrar_entrega(pedido_id, archivo_foto, entregado_por, comentario="", email_enviado=1, error_envio=""):
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    query = '''
        INSERT INTO entregas (pedido_id, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
        VALUES (%s, %s, %s, %s, %s, %s)
    '''
    valores = (pedido_id, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
    cursor.execute(query, valores)
    conn.commit()
    conn.close()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        pedido_id = request.form.get("pedido_id")
        entregado_por = request.form.get("entregado_por")
        comentario = request.form.get("comentario")
        file = request.files["imagen"]

        if not pedido_id or not entregado_por or not file:
            flash("Todos los campos son obligatorios.", "error")
            return redirect(request.url)

        filename = f"entrega_{pedido_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(image_path)

        pedido = obtener_datos_pedido(pedido_id)
        if not pedido:
            flash("Pedido no encontrado.", "error")
            return redirect(request.url)

        correo, asunto, cuerpo = (
            pedido["email"],
            f"Pedido {pedido_id} Entregado",
            f"Hola {pedido['nombre']},\n\nTu pedido #{pedido_id} fue entregado. Adjuntamos imagen.\n\nGracias por elegir PrinterExpress."
        )

        try:
            enviar_correo(correo, asunto, cuerpo, image_path)
            registrar_entrega(pedido_id, image_path, entregado_por, comentario, 1, "")
            flash("✅ Correo enviado y entrega registrada correctamente.", "success")
        except Exception as e:
            registrar_entrega(pedido_id, image_path, entregado_por, comentario, 0, str(e))
            flash(f"⚠️ Error al enviar el correo, pero entrega registrada. Detalle: {e}", "error")

        return redirect(url_for("index"))

    return render_template("formulario.html")

@app.route("/datos_cliente/<int:pedido_id>")
def datos_cliente(pedido_id):
    pedido = obtener_datos_pedido(pedido_id)
    if not pedido:
        return jsonify({"error": "Pedido no encontrado"}), 404

    return jsonify({
        "nombre": pedido["nombre"],
        "direccion": pedido["direccion"],
        "comuna": pedido["comuna"]
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
