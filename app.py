
# Standard Library Imports
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone # Consolidated datetime imports

# Third-Party Library Imports
from dotenv import load_dotenv
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
import mysql.connector
from PIL import Image

# FIXME: Timezone configuration for Chile - Does NOT account for Daylight Saving Time (DST).
# Chile typically observes DST (UTC-3). This fixed offset (UTC-4) will be incorrect during those periods.
# For accurate, DST-aware timestamps, consider using a library like pytz:
# import pytz
# chile_tz = pytz.timezone('America/Santiago')
# Or, for Python 3.9+:
# from zoneinfo import ZoneInfo
# chile_tz = ZoneInfo('America/Santiago')
chile_tz = timezone(timedelta(hours=-4))  # Para horario de invierno Chile continental (Fixed UTC-4)
load_dotenv()

# Note: Files in UPLOAD_FOLDER are publicly accessible via /static/uploads/. Review privacy requirements.
UPLOAD_FOLDER = 'static/uploads'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = os.getenv("APP_SECRET_KEY", 'printerexpress_key')  # Added a default for local dev
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

def obtener_datos_pedido(pedido_id):
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        # Fetches comuna_nombre using a LEFT JOIN.
        # Assumes 'impresiones' table has a 'comuna' field (FK) and 'comunas' table has 'id' and 'nombre'.
        query = """
            SELECT i.*, c.nombre AS comuna_nombre
            FROM impresiones i
            LEFT JOIN comunas c ON i.comuna = c.id
            WHERE i.id = %s
        """
        cursor.execute(query, (pedido_id,))
        pedido = cursor.fetchone()
        return pedido
    except mysql.connector.Error as err:
        app.logger.error(f"Database error in obtener_datos_pedido for pedido_id {pedido_id}: {err}")
        return None
    finally:
        if conn and conn.is_connected():
            conn.close()

def enviar_correo(destinatario, asunto, cuerpo, imagen_path):
    msg = EmailMessage()
    msg["From"] = SMTP_CONFIG["user"]
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.set_content(cuerpo)

    with open(imagen_path, "rb") as f:
        img_data = f.read()
        msg.add_attachment(img_data, maintype="image", subtype="jpeg", filename=os.path.basename(imagen_path))

    server = None  # Initialize server to None
    try:
        server = smtplib.SMTP(SMTP_CONFIG["host"], SMTP_CONFIG["port"])
        server.starttls()
        server.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
        server.send_message(msg)
    # Errors during SMTP operations will propagate to the caller (index route)
    # The primary goal here is to ensure server.quit() is called if server was initialized.
    finally:
        if server:
            try:
                server.quit()
            except smtplib.SMTPServerDisconnected:
                # Log this specific case: server already disconnected
                app.logger.info("SMTP server was already disconnected before quit.")
            except Exception as e:
                # Log other potential errors during quit, but don't let them overshadow original error
                app.logger.warning(f"Error during SMTP server.quit(): {e}")

def registrar_entrega(pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario="", email_enviado=1, error_envio=""):
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        query = """
            INSERT INTO entregas (pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        valores = (pedido_id, fecha_entrega, archivo_foto, entregado_por, comentario, email_enviado, error_envio)
        cursor.execute(query, valores)
        conn.commit()
        app.logger.info(f"Entrega registrada for pedido_id {pedido_id}, email_enviado: {email_enviado}, error: '{error_envio}'")
    except mysql.connector.Error as err:
        app.logger.error(f"Database error in registrar_entrega for pedido_id {pedido_id}: {err}")
        raise  # Re-raise the exception to be handled by the caller
    finally:
        if conn and conn.is_connected():
            conn.close()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        pedido_id = request.form.get("pedido_id")
        entregado_por = request.form.get("entregado_por") or "PrinterExpress"
        comentario = request.form.get("comentario") or "Entregado"

        if "imagen" not in request.files or request.files["imagen"].filename == "":
            flash("Debe adjuntar una imagen de la entrega.", "error")
            return redirect(request.url)
        file = request.files["imagen"]

        # --- Input and Form Validation ---
        if not pedido_id: # entregado_por has a default, so only pedido_id needs explicit check for presence here.
            flash("El ID del pedido es obligatorio.", "error")
            return redirect(request.url)
        
        try:
            # Validate if pedido_id is a number. The actual value used later can still be the string.
            # This is primarily to prevent non-numeric strings from causing issues downstream.
            int(pedido_id) 
        except (ValueError, TypeError):
            flash("ID de Pedido inválido. Debe ser un número.", "error")
            return redirect(request.url)

        if len(entregado_por) > 255:
            flash("El nombre 'Entregado por' es demasiado largo (máx 255 caracteres).", "error")
            return redirect(request.url)
        
        # Assuming a max length of 1000 for comentario, adjust if DB schema differs
        if len(comentario) > 1000: 
            flash("El comentario es demasiado largo (máx 1000 caracteres).", "error")
            return redirect(request.url)

        pedido = obtener_datos_pedido(pedido_id)
        if not pedido:
            flash(f"❌ El pedido #{pedido_id} no existe en la base de datos.", "error")
            return redirect(request.url)

        # --- File Handling & Initial Save ---
        momento_foto = datetime.now(chile_tz)
        fecha_entrega = momento_foto.strftime('%d/%m/%Y %H:%M:%S')

        filename = f"entrega_{pedido_id}_{momento_foto.strftime('%Y%m%d%H%M%S')}.jpg"
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        os.makedirs(os.path.dirname(image_path), exist_ok=True)
        file.save(image_path)
        app.logger.info(f"Image initially saved to {image_path} for order {pedido_id}")

        # --- Image Processing ---
        try:
            img = Image.open(image_path)
            img.thumbnail((1024, 1024))
            img.save(image_path, format='JPEG', quality=50, dpi=(72, 72), optimize=True)
            app.logger.info(f"Image processed and overwritten for {image_path}")
        except Exception as e:
            app.logger.error(f"Image processing error for {image_path} (order {pedido_id}): {e}")
            # Attempt to clean up the initially saved file
            try:
                if os.path.exists(image_path): # Check if file exists before trying to remove
                    os.remove(image_path)
                    app.logger.info(f"Orphaned file {image_path} removed.")
            except OSError as oe:
                app.logger.error(f"Error removing orphaned file {image_path}: {oe}")
            
            flash("⚠️ Hubo un problema al procesar la imagen. Inténtalo de nuevo.", "error")
            return redirect(request.url)

        # --- Email Preparation & Sending ---
        correo = pedido["email"]
        asunto = f"Pedido {pedido_id} Entregado"
        cuerpo = (
            f"Hola {pedido['nombre']},\n\n"
            f"Queremos contarte que tu pedido número {pedido_id} ha sido entregado con éxito el día {momento_foto}.\n\n"
            f"Adjuntamos una imagen como respaldo de la entrega.\n\n"
            f"Gracias por preferirnos.\n\n"
            f"Un saludo afectuoso,\n"
            f"Equipo de Repartos\n"
            f"PrinterExpress Spa"
        )

        try:
            enviar_correo(correo, asunto, cuerpo, image_path)
            enviar_correo(correo, asunto, cuerpo, image_path)
            app.logger.info(f"Email sent to {correo} for order {pedido_id}")
            registrar_entrega(pedido_id, fecha_entrega, image_path, entregado_por, comentario, 1, "")
            # Note: registrar_entrega logs its own success/failure for DB part
            flash("✅ Correo enviado y entrega registrada correctamente.", "success")
        except Exception as e:
            # This is the outer exception (e.g., email sending failed, or first registrar_entrega failed before email)
            app.logger.error(f"Email sending or initial registration failed for order {pedido_id}: {e}")
            try:
                # Attempt to register the delivery with error flags from the email sending failure
                registrar_entrega(pedido_id, fecha_entrega, image_path, entregado_por, comentario, 0, str(e))
                # Note: registrar_entrega logs its own success/failure for DB part
                flash(f"⚠️ Error al enviar el correo, pero entrega registrada con error. Detalle: {e}", "error")
            except Exception as nested_db_error:
                # This is if the *second* attempt to registrar_entrega also fails
                app.logger.critical(f"Failed to register delivery for order {pedido_id} even after email error. Initial error: {e}. DB error: {nested_db_error}")
                flash("❌ Error crítico: No se pudo registrar el estado de la entrega. Contacte a soporte.", "error")
        
        return redirect(url_for("index"))

    return render_template("formulario.html")

@app.route("/datos_cliente/<int:pedido_id>")
def datos_cliente(pedido_id):
    pedido = obtener_datos_pedido(pedido_id) # This now includes comuna_nombre
    if not pedido:
        return jsonify({"error": "Pedido no encontrado o error de base de datos"}), 404

    # comuna_nombre is now part of the pedido dictionary from obtener_datos_pedido
    # Default to "-" if comuna_nombre is None (e.g., LEFT JOIN found no match) or not present
    comuna_nombre = pedido.get("comuna_nombre") if pedido.get("comuna_nombre") is not None else "-"

    return jsonify({
        "nombre": pedido.get("nombre", "-"), # Use .get for safety
        "direccion": pedido.get("direccion", "-"), # Use .get for safety
        "comuna": comuna_nombre
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
