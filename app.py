from flask import Flask, render_template, request, jsonify, send_file
import sqlite3
# pyrefly: ignore [missing-import]
import mysql.connector
from groq import Groq
import os
import io
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)

# ==========================================
# 1. CONFIGURACIÓN DE GROQ API
# ==========================================
groq_client = None
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("❌ Error: GROQ_API_KEY no configurada en .env")
else:
    groq_client = Groq(api_key=api_key)

# ==========================================
# 2. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
def conectar_bd():
    sqlite_db = os.getenv("SQLITE_DB") or "control_nomina.db"
    if not os.path.exists(sqlite_db) and os.path.exists("nomina_empresa.db"):
        sqlite_db = "nomina_empresa.db"

    if os.path.exists(sqlite_db):
        conn = sqlite3.connect(sqlite_db)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "12345"),
        database=os.getenv("MYSQL_DATABASE", "control_nomina")
    ), "mysql"


def asegurar_esquema_sqlite():
    # Ya no forzamos la creación de tablas antiguas.
    # Usaremos setup_db.py para la estructura correcta.
    pass

asegurar_esquema_sqlite()


def obtener_empleados_bd():
    conn, motor = conectar_bd()
    try:
        if motor == "sqlite":
            cursor = conn.cursor()
            # Consultamos la nueva tabla mexicana
            cursor.execute("""
                SELECT id, nombre, rfc, curp, nss, sdi, tipo_contrato, fecha_ingreso, departamento
                FROM empleados
                ORDER BY id
            """)
            filas = cursor.fetchall()
            return [
                {
                    "id": fila[0],
                    "nombre": fila[1],
                    "rfc": fila[2],
                    "curp": fila[3],
                    "nss": fila[4],
                    "sdi": float(fila[5]),
                    "tipo_contrato": fila[6],
                    "fecha_ingreso": fila[7],
                    # Mapeo de compatibilidad temporal para UI existente
                    "salario_hora": float(fila[5]), 
                    "puesto": fila[6],
                    "departamento": fila[8] or "Sin departamento"
                }
                for fila in filas
            ]

        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.id_empleado, e.nombre, e.apellido, p.nombre AS puesto,
                   p.salario_por_hora, e.vacaciones_disponibles
            FROM empleados e
            LEFT JOIN puestos p ON e.id_puesto = p.id_puesto
            WHERE e.activo = 1
        """)
        filas = cursor.fetchall()
        return [
            {
                "id": fila['id_empleado'],
                "nombre": f"{fila['nombre']} {fila['apellido']}",
                "puesto": fila['puesto'] or "Sin departamento",
                "salario_hora": float(fila['salario_por_hora']),
                "vacaciones_disponibles": fila['vacaciones_disponibles'],
            }
            for fila in filas
        ]
    except Exception as e:
        print(f"Error en obtener_empleados_bd: {e}")
        return []
    finally:
        conn.close()


def insertar_periodo_y_recibo_prueba(empleado_id, dias_pagados=15):
    """Función para crear un periodo e insertar un recibo de nómina de prueba basado en SDI."""
    conn, motor = conectar_bd()
    if motor != "sqlite":
        return False
    
    try:
        cursor = conn.cursor()
        
        # Crear periodo si no existe
        cursor.execute("SELECT id FROM nominas_periodos LIMIT 1")
        periodo = cursor.fetchone()
        if not periodo:
            cursor.execute("INSERT INTO nominas_periodos (fecha_inicio, fecha_fin, dias_pagados) VALUES ('2026-06-01', '2026-06-15', ?)", (dias_pagados,))
            periodo_id = cursor.lastrowid
        else:
            periodo_id = periodo[0]
            
        # Verificar si ya tiene recibo
        cursor.execute("SELECT id FROM detalle_nomina WHERE empleado_id = ? AND periodo_id = ?", (empleado_id, periodo_id))
        if cursor.fetchone():
            return True # Ya existe
            
        # Obtener datos empleado
        cursor.execute("SELECT sdi FROM empleados WHERE id = ?", (empleado_id,))
        emp = cursor.fetchone()
        if not emp:
            return False
            
        sdi = emp[0]
        sueldo_base = sdi * dias_pagados
        retencion_isr = sueldo_base * 0.10 # 10% fijo de prueba
        cuota_imss = sueldo_base * 0.02375 # Porcentaje LSS genérico obrero
        
        cursor.execute("SELECT id, nombre_concepto FROM conceptos")
        conceptos = {row[1]: row[0] for row in cursor.fetchall()}
        
        detalles = [
            (empleado_id, periodo_id, conceptos.get("Sueldo Base", 1), sueldo_base),
            (empleado_id, periodo_id, conceptos.get("Retencion ISR", 5), retencion_isr),
            (empleado_id, periodo_id, conceptos.get("Cuota IMSS", 6), cuota_imss)
        ]
        
        cursor.executemany("""
            INSERT INTO detalle_nomina (empleado_id, periodo_id, concepto_id, monto)
            VALUES (?, ?, ?, ?)
        """, detalles)
        
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Error en inserción de prueba: {e}")
        return False
    finally:
        conn.close()


def obtener_recibo_nomina(empleado_id, periodo_id=1):
    """Consulta segura usando fetchall para calcular sueldo neto."""
    conn, motor = conectar_bd()
    if motor != "sqlite":
        return calcular_nomina_simple({"salario_hora": 0})
        
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.nombre_concepto, c.tipo, d.monto
            FROM detalle_nomina d
            JOIN conceptos c ON d.concepto_id = c.id
            WHERE d.empleado_id = ? AND d.periodo_id = ?
        """, (empleado_id, periodo_id))
        
        filas = cursor.fetchall()
        
        if not filas:
            # Si no hay recibo, generar uno de prueba automáticamente
            insertar_periodo_y_recibo_prueba(empleado_id)
            cursor.execute("""
                SELECT c.nombre_concepto, c.tipo, d.monto
                FROM detalle_nomina d
                JOIN conceptos c ON d.concepto_id = c.id
                WHERE d.empleado_id = ? AND d.periodo_id = ?
            """, (empleado_id, periodo_id))
            filas = cursor.fetchall()
            
        percepciones = 0.0
        deducciones = 0.0
        
        for nombre, tipo, monto in filas:
            if tipo == 'Percepcion':
                percepciones += monto
            else:
                deducciones += monto
                
        neto = percepciones - deducciones
        
        # Formato compatible con el resto del backend actual
        return {
            "total_bruto": percepciones,
            "isr": deducciones, # Aquí englobamos IMSS + ISR por ahora para UI
            "total_neto": neto,
            "percepciones": percepciones,
            "deducciones": deducciones
        }
    except Exception as e:
        print(f"Error obteniendo recibo: {e}")
        return {"total_bruto": 0, "isr": 0, "total_neto": 0}
    finally:
        conn.close()


def calcular_nomina_simple(empleado, horas_normales=40, horas_extras=0):
    pago_normal = horas_normales * empleado['salario_hora']
    pago_extra = horas_extras * (empleado['salario_hora'] * 2)
    total_bruto = pago_normal + pago_extra
    isr = total_bruto * 0.10
    total_neto = total_bruto - isr
    return {
        "horas_normales": horas_normales,
        "horas_extras": horas_extras,
        "pago_normal": pago_normal,
        "pago_extra": pago_extra,
        "total_bruto": total_bruto,
        "isr": isr,
        "total_neto": total_neto,
    }


def obtener_contexto_empleados():
    try:
        conn, motor = conectar_bd()

        if motor == "sqlite":
            cursor = conn.cursor()
            query = """
                SELECT id AS id_empleado,
                       nombre,
                       rfc,
                       sdi,
                       tipo_contrato
                FROM empleados
            """
            cursor.execute(query)
            filas = cursor.fetchall()
            cursor.close()
            conn.close()

            contexto = "BASE DE DATOS DE EMPLEADOS ACTUALES (MÉXICO):\n"
            for f in filas:
                contexto += (
                    f"- ID: {f['id_empleado']}, Nombre: {f['nombre']}, "
                    f"RFC: {f['rfc']}, SDI: ${f['sdi']} MXN, "
                    f"Contrato: {f['tipo_contrato']}.\n"
                )
            return contexto

        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT e.id_empleado, e.nombre, e.apellido, p.nombre AS puesto,
                   p.salario_por_hora, e.vacaciones_disponibles
            FROM empleados e
            LEFT JOIN puestos p ON e.id_puesto = p.id_puesto
            WHERE e.activo = 1
        """
        cursor.execute(query)
        filas = cursor.fetchall()
        cursor.close()
        conn.close()

        contexto = "BASE DE DATOS DE EMPLEADOS ACTUALES:\n"
        for f in filas:
            contexto += (
                f"- ID: {f['id_empleado']}, Nombre: {f['nombre']} {f['apellido']}, "
                f"Puesto: {f['puesto']}, Salario por Hora: ${f['salario_por_hora']} MXN, "
                f"Vacaciones: {f['vacaciones_disponibles']} días.\n"
            )
        return contexto
    except Exception as e:
        return f"Error de BD: No se pudo cargar el contexto. Detalles: {e}"

# ==========================================
# 3. RUTAS WEB (CONEXIÓN CON HTML)
# ==========================================
@app.route('/')
def home():
    # Esto carga tu archivo index.html desde la carpeta 'templates'
    return render_template('index.html')

@app.route('/api/nominas', methods=['GET'])
def api_nominas():
    try:
        conn, motor = conectar_bd()

        if motor == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, nombre, rfc, sdi, tipo_contrato
                FROM empleados
                ORDER BY id
            """)
            filas = cursor.fetchall()
            cursor.close()
            conn.close()

            nominas = []
            for fila in filas:
                recibo = obtener_recibo_nomina(fila[0])
                if not recibo:
                    continue
                nominas.append({
                    "nombre": fila[1],
                    "periodo": "Quincena actual",
                    "horasNormales": 15,
                    "horasExtras": 0,
                    "totalBruto": f"{recibo['total_bruto']:.2f}",
                    "departamento": fila[4] or "Sin contrato",
                    "fecha": "BD"
                })
            return jsonify({"nominas": nominas})

        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT e.id_empleado, e.nombre, e.apellido, p.nombre AS puesto,
                   p.salario_por_hora, e.vacaciones_disponibles
            FROM empleados e
            LEFT JOIN puestos p ON e.id_puesto = p.id_puesto
            WHERE e.activo = 1
        """)
        filas = cursor.fetchall()
        cursor.close()
        conn.close()

        nominas = []
        for fila in filas:
            horas_normales = 40
            total_bruto = round(float(fila['salario_por_hora']) * horas_normales, 2)
            nominas.append({
                "nombre": f"{fila['nombre']} {fila['apellido']}",
                "periodo": "Mes actual",
                "horasNormales": horas_normales,
                "horasExtras": 0,
                "totalBruto": f"{total_bruto:.2f}",
                "departamento": fila['puesto'] or "Sin departamento",
                "fecha": "BD"
            })
        return jsonify({"nominas": nominas})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/departamentos', methods=['GET'])
def api_departamentos():
    conn, motor = conectar_bd()
    try:
        if motor == 'sqlite':
            cursor = conn.cursor()
            cursor.execute("SELECT id, nombre FROM departamentos ORDER BY nombre")
            return jsonify({"departamentos": [{"id": fila[0], "nombre": fila[1]} for fila in cursor.fetchall()]})
        return jsonify({"departamentos": [{"id": 1, "nombre": "Sistemas"}, {"id": 2, "nombre": "Contabilidad"}]})
    finally:
        conn.close()


@app.route('/api/departamentos', methods=['POST'])
def crear_departamento():
    datos = request.get_json(silent=True) or {}
    nombre = (datos.get('nombre') or '').strip()
    if not nombre:
        return jsonify({"error": "Nombre del departamento obligatorio."}), 400

    conn, motor = conectar_bd()
    try:
        if motor == 'sqlite':
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO departamentos (nombre) VALUES (?)", (nombre,))
            conn.commit()
            return jsonify({"ok": True, "mensaje": "Departamento creado."})
        return jsonify({"ok": True, "mensaje": "Departamento creado."})
    finally:
        conn.close()


@app.route('/api/empleados', methods=['GET'])
def api_empleados():
    return jsonify({"empleados": obtener_empleados_bd()})


@app.route('/api/empleados', methods=['POST'])
def crear_empleado():
    datos = request.get_json(silent=True) or {}
    nombre = (datos.get('nombre') or '').strip()
    puesto = (datos.get('puesto') or '').strip()
    departamento = (datos.get('departamento') or '').strip()
    salario_hora = float(datos.get('salario_hora') or 0)
    vacaciones = int(datos.get('vacaciones_disponibles') or 0)

    if not nombre or salario_hora <= 0:
        return jsonify({"error": "Nombre y salario por hora son obligatorios."}), 400

    conn, motor = conectar_bd()
    try:
        if motor == 'sqlite':
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO empleados (nombre, rfc, curp, nss, sdi, tipo_contrato, fecha_ingreso, departamento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (nombre, "RFC-GENERICO", "CURP-GENERICO", "0000000000", salario_hora, puesto, "2026-01-01", departamento),
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO empleados (nombre, apellido, id_puesto, activo, vacaciones_disponibles) VALUES (%s, %s, %s, %s, %s)",
                (nombre, '', 1, 1, vacaciones),
            )
        conn.commit()
        return jsonify({"ok": True, "mensaje": "Empleado creado correctamente."})
    finally:
        conn.close()


@app.route('/api/empleados/<int:empleado_id>', methods=['PUT'])
def actualizar_empleado(empleado_id):
    datos = request.get_json(silent=True) or {}
    nombre = (datos.get('nombre') or '').strip()
    puesto = (datos.get('puesto') or '').strip()
    departamento = (datos.get('departamento') or '').strip()
    salario_hora = float(datos.get('salario_hora') or 0)
    vacaciones = int(datos.get('vacaciones_disponibles') or 0)

    if not nombre or salario_hora <= 0:
        return jsonify({"error": "Nombre y salario por hora son obligatorios."}), 400

    conn, motor = conectar_bd()
    try:
        if motor == 'sqlite':
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE empleados SET nombre=?, sdi=?, tipo_contrato=?, departamento=? WHERE id=?",
                (nombre, salario_hora, puesto, departamento, empleado_id),
            )
        else:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE empleados SET nombre=%s, apellido=%s WHERE id_empleado=%s",
                (nombre, '', empleado_id),
            )
        conn.commit()
        return jsonify({"ok": True, "mensaje": "Empleado actualizado correctamente."})
    finally:
        conn.close()


@app.route('/api/empleados/<int:empleado_id>', methods=['DELETE'])
def eliminar_empleado(empleado_id):
    conn, motor = conectar_bd()
    try:
        if motor == 'sqlite':
            cursor = conn.cursor()
            cursor.execute("DELETE FROM empleados WHERE id=?", (empleado_id,))
        else:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM empleados WHERE id_empleado=%s", (empleado_id,))
        conn.commit()
        return jsonify({"ok": True, "mensaje": "Empleado eliminado correctamente."})
    finally:
        conn.close()


@app.route('/api/pdf/nomina/<int:empleado_id>', methods=['GET'])
def pdf_nomina(empleado_id):
    empleados = obtener_empleados_bd()
    empleado = next((e for e in empleados if e['id'] == empleado_id), None)
    if not empleado:
        return jsonify({"error": "Empleado no encontrado."}), 404

    resumen = obtener_recibo_nomina(empleado['id'])
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle(f"Nomina-{empleado['nombre']}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(60, 770, "Nómina de Empleado (México)")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(60, 740, f"Empleado: {empleado['nombre']}")
    pdf.drawString(60, 720, f"RFC: {empleado['rfc']}")
    pdf.drawString(60, 700, f"Salario Diario Integrado (SDI): ${empleado['sdi']:.2f}")
    pdf.drawString(60, 680, f"Días pagados: 15")
    pdf.drawString(60, 640, f"Percepciones Totales: ${resumen['percepciones']:.2f}")
    pdf.drawString(60, 620, f"Deducciones Totales (ISR/IMSS): ${resumen['deducciones']:.2f}")
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, 590, f"Sueldo Neto a Pagar: ${resumen['total_neto']:.2f}")
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, download_name=f"nomina_{empleado_id}.pdf", as_attachment=True, mimetype='application/pdf')


@app.route('/api/pdf/nominas_multiples', methods=['GET'])
def pdf_nominas_multiples():
    ids_param = request.args.get('ids')
    if not ids_param:
        return jsonify({"error": "No se proporcionaron IDs."}), 400
    
    try:
        ids = [int(i) for i in ids_param.split(',')]
    except ValueError:
        return jsonify({"error": "IDs inválidos."}), 400

    empleados = obtener_empleados_bd()
    empleados_seleccionados = [e for e in empleados if e['id'] in ids]
    
    if not empleados_seleccionados:
        return jsonify({"error": "No se encontraron empleados."}), 404

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle("Nóminas Múltiples")
    
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(60, 770, "Reporte de Nóminas")
    
    y = 740
    total_general = 0
    pdf.setFont("Helvetica", 11)
    
    for emp in empleados_seleccionados:
        if y < 100:
            pdf.showPage()
            pdf.setFont("Helvetica", 11)
            y = 750
            
        resumen = obtener_recibo_nomina(emp['id'])
        pdf.drawString(60, y, f"Empleado: {emp['nombre']} (RFC: {emp['rfc']}) - Sueldo Neto: ${resumen['total_neto']:.2f}")
        total_general += resumen['total_neto']
        y -= 20
        
    y -= 10
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, f"Total de los Totales: ${total_general:.2f}")
    
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, download_name="reporte_nominas.pdf", as_attachment=True, mimetype='application/pdf')


@app.route('/api/chat', methods=['POST'])
def chat():
    datos = request.json
    mensaje_usuario = datos.get('mensaje')
    
    # Construcción del prompt del sistema dinámico con los datos de SQL
    contexto_db = obtener_contexto_empleados()
    system_instruction = f"""
    Eres un Chatbot experto en Legislación Laboral de México (LFT, LSS, ISR) y Gestión de Nómina.
    
    {contexto_db}
    
    REGLAS CRÍTICAS DE OPERACIÓN:
    1. Tienes la capacidad y autorización para crear y generar archivos PDF. NUNCA digas que eres solo texto o que no puedes crear PDFs.
    2. Al calcular la nómina de un empleado de la BD, utiliza su Salario Diario Integrado (SDI) multiplicado por 15 días pagados (quincena) como Sueldo Base.
    3. Para deducciones, asume una retención simplificada de ISR del 10% y una Cuota Obrera IMSS del 2.375% sobre el Sueldo Base. Sueldo Neto = Sueldo Base - ISR - IMSS.
    4. Para entregar el PDF de un empleado, añade siempre al final de tu respuesta el siguiente enlace HTML exacto:
       <br><br><a href='/api/pdf/nomina/ID_AQUI' target='_blank'>📥 Descargar PDF de NOMBRE_AQUI</a>
       (Reemplaza ID_AQUI por el ID numérico del empleado).
    5. Si el usuario pide un PDF o reporte de varios empleados, añade:
       <br><br><a href='/api/pdf/nominas_multiples?ids=ID1,ID2,ID3' target='_blank'>📥 Descargar Reporte PDF</a>
    6. Formatea tu respuesta con etiquetas HTML como <br> para saltos de línea y <strong> para negritas. Explica brevemente la retención de IMSS y ISR como conceptos mexicanos.
    """
    
    try:
        mensaje = (mensaje_usuario or '').strip().lower()
        empleados = obtener_empleados_bd()

        if 'nomina' in mensaje or 'nómina' in mensaje or 'pdf' in mensaje or 'lista' in mensaje or 'total' in mensaje:
            if 'todos' in mensaje or 'varios' in mensaje:
                coincidencias = empleados
            else:
                coincidencias = [e for e in empleados if e['nombre'].lower() in mensaje]

            if len(coincidencias) == 1:
                emp = coincidencias[0]
                resumen = obtener_recibo_nomina(emp['id'])
                texto = (
                    f"Claro. Aquí tienes la información de la nómina de {emp['nombre']}:<br>"
                    f"Sueldo Base (Percepciones): ${resumen['percepciones']:.2f}<br>"
                    f"Retenciones (ISR/IMSS): ${resumen['deducciones']:.2f}<br>"
                    f"Sueldo Neto: ${resumen['total_neto']:.2f}<br>"
                    f"<a href='/api/pdf/nomina/{emp['id']}' target='_blank'>📥 Descargar PDF de {emp['nombre']}</a>"
                )
                return jsonify({"respuesta": texto})
            elif len(coincidencias) > 1:
                total_general = 0.0
                partes = []
                ids = []
                for emp in coincidencias:
                    resumen = obtener_recibo_nomina(emp['id'])
                    total_general += resumen['total_neto']
                    partes.append(f"• {emp['nombre']}: ${resumen['total_neto']:.2f} (Neto)")
                    ids.append(str(emp['id']))
                    
                ids_str = ",".join(ids)
                texto = (
                    "<strong>Resumen de nóminas solicitado:</strong><br>" + 
                    "<br>".join(partes) + 
                    f"<br><br><strong>Total de los totales:</strong> ${total_general:.2f}<br>"
                    f"<a href='/api/pdf/nominas_multiples?ids={ids_str}' target='_blank'>Descargar PDF del reporte</a>"
                )
                return jsonify({"respuesta": texto})

        if groq_client is None:
            return jsonify({"respuesta": "<strong style='color:red;'>Error de la IA:</strong> GROQ_API_KEY no configurada."})

        respuesta = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": mensaje_usuario},
            ],
        )

        texto = respuesta.choices[0].message.content or ""
        texto_html = texto.replace('\n', '<br>')
        return jsonify({"respuesta": texto_html})
        
    except Exception as e:
        return jsonify({"respuesta": f"<strong style='color:red;'>Error de la IA:</strong> {e}"})

if __name__ == '__main__':
    app.run(debug=True)