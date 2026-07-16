import os
import sys
import sqlite3
import mysql.connector
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

# Cargar variables de entorno
load_dotenv()

try:
    sqlite_db = os.getenv("SQLITE_DB") or "control_nomina.db"
    if not os.path.exists(sqlite_db) and os.path.exists("nomina_empresa.db"):
        sqlite_db = "nomina_empresa.db"

    if os.path.exists(sqlite_db):
        conn = sqlite3.connect(sqlite_db)
        cursor = conn.cursor()
        print(f"✅ Conectado a SQLite exitosamente: {sqlite_db}\n")
        motor = "sqlite"
    else:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "12345"),
            database=os.getenv("MYSQL_DATABASE", "control_nomina")
        )
        cursor = conn.cursor()
        print("✅ Conectado a MySQL exitosamente\n")
        motor = "mysql"
    
    # Crear tablas si no existen
    print("📋 Creando tablas...\n")
    
    if motor == "sqlite":
        # Habilitar Foreign Keys en SQLite
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # Eliminar tablas antiguas para actualizar esquema
        cursor.execute("DROP TABLE IF EXISTS detalle_nomina")
        cursor.execute("DROP TABLE IF EXISTS nominas_periodos")
        cursor.execute("DROP TABLE IF EXISTS conceptos")
        cursor.execute("DROP TABLE IF EXISTS empleados")
        cursor.execute("DROP TABLE IF EXISTS departamentos")

        # Tabla Departamentos
        cursor.execute("""
            CREATE TABLE departamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE
            )
        """)

        # 1. Tabla Empleados
        cursor.execute("""
            CREATE TABLE empleados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                rfc TEXT,
                curp TEXT,
                nss TEXT,
                sdi REAL NOT NULL,
                tipo_contrato TEXT,
                fecha_ingreso TEXT,
                departamento TEXT
            )
        """)
        
        # 2. Tabla Conceptos
        cursor.execute("""
            CREATE TABLE conceptos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre_concepto TEXT NOT NULL,
                tipo TEXT NOT NULL,  -- 'Percepcion' o 'Deduccion'
                graba_isr BOOLEAN NOT NULL
            )
        """)
        
        # 3. Tabla Nominas_Periodos
        cursor.execute("""
            CREATE TABLE nominas_periodos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha_inicio TEXT NOT NULL,
                fecha_fin TEXT NOT NULL,
                dias_pagados INTEGER NOT NULL
            )
        """)
        
        # 4. Tabla Detalle_Nomina
        cursor.execute("""
            CREATE TABLE detalle_nomina (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                empleado_id INTEGER NOT NULL,
                periodo_id INTEGER NOT NULL,
                concepto_id INTEGER NOT NULL,
                monto REAL NOT NULL,
                FOREIGN KEY(empleado_id) REFERENCES empleados(id),
                FOREIGN KEY(periodo_id) REFERENCES nominas_periodos(id),
                FOREIGN KEY(concepto_id) REFERENCES conceptos(id)
            )
        """)
        print("✓ Tablas mexicanas (empleados, conceptos, nominas, detalle) creadas con FKs.")
        
        # Insertar Conceptos de Prueba
        conceptos = [
            ("Sueldo Base", "Percepcion", True),
            ("Horas Extra", "Percepcion", True),
            ("Prima Vacacional", "Percepcion", True),
            ("Aguinaldo", "Percepcion", True),
            ("Retencion ISR", "Deduccion", False),
            ("Cuota IMSS", "Deduccion", False)
        ]
        cursor.executemany("INSERT INTO conceptos (nombre_concepto, tipo, graba_isr) VALUES (?, ?, ?)", conceptos)
        
        # Insertar Empleados de Prueba
        empleados_demo = [
            ("Juan Perez", "PEPJ900101XYZ", "PEPJ900101HDFRXYZ", "12345678901", 500.0, "Indeterminado", "2023-01-15", "Sistemas"),
            ("Maria Rodriguez", "ROGM920515ABC", "ROGM920515MDFRABC", "10987654321", 650.0, "Indeterminado", "2022-06-01", "Contabilidad")
        ]
        cursor.executemany(
            "INSERT INTO empleados (nombre, rfc, curp, nss, sdi, tipo_contrato, fecha_ingreso, departamento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            empleados_demo
        )
        
        # Insertar Departamentos
        cursor.executemany("INSERT INTO departamentos (nombre) VALUES (?)", [("Sistemas",), ("Contabilidad",), ("Recursos Humanos",)])
        
        conn.commit()
        print("✓ Conceptos, Departamentos y Empleados insertados.\n")

    else:
        # Lógica MySQL existente sin modificar para no romper si lo usa
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS puestos (
                id_puesto INT PRIMARY KEY AUTO_INCREMENT,
                nombre VARCHAR(100) NOT NULL,
                salario_por_hora DECIMAL(10, 2) NOT NULL
            )
        """)
        print("✓ Tabla 'puestos' lista")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS empleados (
                id_empleado INT PRIMARY KEY AUTO_INCREMENT,
                nombre VARCHAR(100) NOT NULL,
                apellido VARCHAR(100) NOT NULL,
                id_puesto INT,
                activo INT DEFAULT 1,
                vacaciones_disponibles INT DEFAULT 15,
                FOREIGN KEY (id_puesto) REFERENCES puestos(id_puesto)
            )
        """)
        print("✓ Tabla 'empleados' lista\n")
        
        print("🏢 Insertando puestos de ejemplo...\n")
        puestos = [
            ("Contador", 250),
            ("Técnico de Sistemas", 300),
            ("Operario", 150),
            ("Gerente", 400),
            ("Asistente Administrativo", 180)
        ]

        for puesto, salario in puestos:
            cursor.execute(
                "INSERT IGNORE INTO puestos (nombre, salario_por_hora) VALUES (%s, %s)",
                (puesto, salario)
            )

        conn.commit()
        print(f"✓ {len(puestos)} puestos insertados\n")

        print("👥 Insertando empleados de ejemplo...\n")
        empleados = [
            ("Juan", "Pérez", 1, 1, 15),
            ("María", "García", 2, 1, 15),
            ("Carlos", "López", 3, 1, 12),
            ("Ana", "Martínez", 1, 1, 15),
            ("Issacc", "Angelito", 3, 1, 15),
            ("Roberto", "Sánchez", 4, 1, 20),
            ("Laura", "Rodríguez", 5, 1, 15),
            ("Pedro", "Gómez", 2, 1, 14),
        ]

        for nombre, apellido, id_puesto, activo, vacaciones in empleados:
            cursor.execute(
                "INSERT IGNORE INTO empleados (nombre, apellido, id_puesto, activo, vacaciones_disponibles) VALUES (%s, %s, %s, %s, %s)",
                (nombre, apellido, id_puesto, activo, vacaciones)
            )
        conn.commit()
        print(f"✓ {len(empleados)} empleados insertados\n")

    # Mostrar empleados registrados
    print("=" * 70)
    print("📊 EMPLEADOS REGISTRADOS EN LA BASE DE DATOS:")
    print("=" * 70 + "\n")
    
    if motor == "sqlite":
        cursor.execute("""
            SELECT id, nombre, rfc, sdi, fecha_ingreso
            FROM empleados
            ORDER BY id
        """)

        for row in cursor.fetchall():
            print(f"ID: {row[0]} | {row[1]} | RFC: {row[2]} | SDI: ${row[3]} | Ingreso: {row[4]}")
    else:
        cursor.execute("""
            SELECT e.id_empleado, e.nombre, e.apellido, p.nombre AS puesto,
                   p.salario_por_hora, e.vacaciones_disponibles
            FROM empleados e
            LEFT JOIN puestos p ON e.id_puesto = p.id_puesto
            WHERE e.activo = 1
            ORDER BY e.id_empleado
        """)

        for row in cursor.fetchall():
            print(f"ID: {row[0]} | {row[1]} {row[2]} | Puesto: {row[3]} | $${row[4]}/hr | Vacaciones: {row[5]} días")
    
    print("\n" + "=" * 70)
    print("✅ ¡Base de datos configurada exitosamente!")
    print("=" * 70)
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"❌ Error: {e}")
    print("\n⚠️ Verifica que:")
    print("   1. MySQL/XAMPP esté corriendo")
    print("   2. La base de datos 'control_nomina' exista")
    print("   3. Las credenciales en .env sean correctas")
