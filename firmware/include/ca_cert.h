#pragma once
// Este archivo lo REEMPLAZA backend/scripts/generar_certificados.py con el certificado de su CA propia:
//   cd backend
//   python scripts/generar_certificados.py --ip <IP de la laptop servidor>
// Mientras esté vacío, el gateway no podrá conectarse por HTTPS (lo avisa al arrancar).
static const char CA_CERT[] = "";
