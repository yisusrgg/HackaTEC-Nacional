# Propuesta: confiabilidad de componentes

Documento de trabajo del equipo. Resume hacia dónde reorganizar la plataforma y qué se decidió sobre la propuesta «Nueva arquitectura de pantallas» (31 pantallas). No describe código ya hecho.

## 1. Enfoque

- La plataforma es **100 % para Volvo** (Ingeniería y servicio), no para el cliente que compra el autobús.
- Pregunta central: **¿los componentes duran lo que promete el fabricante?** Por ejemplo, si la guillotina del sanitario o la bomba de descarga fallan antes de los ciclos de garantía, hay que saberlo, saber por qué y en qué condiciones (clima, zona, tipo de operación).
- Todos los ingenieros ven toda la flota. El operador (cliente) es solo un dato de la unidad para filtrar y cruzar.
- Los técnicos son de Volvo o de talleres con convenio; los de convenio solo ven lo que se les asigna.

## 2. El concepto que falta: el componente

Hoy la plataforma sabe de **señales** (temperatura, nivel, voltaje…), pero no de **piezas**. Para hablar de garantía hace falta:

| Concepto | Qué es |
|---|---|
| Tipo de componente | Catálogo: nombre, subsistema, fabricante, modelo, vida garantizada en ciclos u horas, límite de falla prematura (p. ej. 50 % de la garantía), regla de conteo. |
| Componente instalado | Una pieza concreta en una unidad: número de serie, fecha de instalación, ciclos y horas acumulados. |
| Falla | Se crea al cerrar una orden: componente retirado, causa, ciclos u horas al fallar, ¿prematura? |
| Causa | Lista cerrada para que todos registren igual y el Pareto sirva. |

Riesgo conocido: `san.usos` se reinicia cuando el nodo se reinicia. El servidor debe sumar diferencias y detectar cuando el contador baja; no tomar el valor tal cual.

## 3. Diagnóstico con nivel de certeza

Cada diagnóstico que propone el sistema lleva un nivel:

- **Confirmado**: el técnico lo confirmó en la orden.
- **Probable**: una regla lo propone con datos claros (p. ej. ventilador en 0 rpm con temperatura subiendo).
- **Sin determinar**: hay síntoma, pero los datos no alcanzan (p. ej. sensor con pocas lecturas válidas).

Las cuentas de sanitario se marcan como **estimadas** (por usos), porque no hay un sensor directo en la bomba ni en la guillotina.

## 4. Decisiones sobre la propuesta de 31 pantallas

### Se excluyen

| Pantalla | Motivo |
|---|---|
| P26 Operadores | No aporta al objetivo y expone información de los clientes que puede ser privada. Los clientes se siguen insertando directo en la base y se usan solo como filtro. Ninguna pantalla muestra sus datos de contacto; solo el nombre comercial. |
| P27 Talleres y convenios | Innecesaria. Al técnico de convenio se le da una cuenta con su rol; si el convenio termina, se desactiva la cuenta. |

Consecuencias de quitarlas:

- Desaparecen CU-19, la primera parte de CU-24, el bloqueo por «convenio vencido» al iniciar sesión y el aviso de 15 días.
- El técnico de convenio es un rol más; a lo mucho, un campo opcional «empresa» en el usuario.
- Región y clima no van en el operador: dependen de dónde circula la unidad. Se propone un campo «región» en la unidad (cambio de modelo, por confirmar).

### Imprescindibles (núcleo de la demo)

| Pantalla | Nota |
|---|---|
| P20 Catálogo de componentes | Sin esto no hay contra qué comparar. Puede empezar con 4–5 tipos cargados por `inicializar`. |
| P12 Componente instalado | La pieza con su barra de vida y su historial. |
| P10 Ficha de la unidad | Junta Autobuses y Monitoreo (ya casi hechos). Para la demo: pestañas Resumen, Componentes, En vivo y Dispositivos. |
| P16 Detalle de la orden | Agregar causa, componente retirado y nuevo, ciclos al fallar y marca de falla prematura. Es lo que genera la falla. |
| P05 Confiabilidad de componentes | Vida a la falla contra garantía por tipo de componente. |
| P13 Alertas | «Tomar» y diagnóstico probable con certeza (reglas fijas en código para la demo). |
| P04 Panorama | Reemplaza el Resumen actual. Versión corta: indicadores, «Atención ahora» y los 10 componentes más cerca de su límite. |

### Se quedan, ya existen o cuestan poco

- P01–P03, P25, P28–P31: ya existen.
- P09 Unidades y P24 Dispositivos: salen de dividir la pantalla actual de Autobuses.
- P08 Calidad de los datos: sale del Análisis actual; los contadores de nodos ya existen.
- P23 Señales y umbrales: el modelo de señales ya existe; es un formulario más el registro en la bitácora.
- P19 Historial y exportación: ya existe; la fuente «resumen por hora» llega después.
- P14 Órdenes y P15 Nueva orden: cambios menores (componente, asignación, «Mis pendientes»).

### Se posponen

| Pantalla | Motivo |
|---|---|
| P07 Comportamiento de variables | Percentiles por día en MySQL no son triviales y dependen del resumen por hora. Vistosa, pero cara. |
| P17 / P18 Investigaciones | La propia propuesta las marca como opcionales. |
| P22 Reglas de diagnóstico | El % de acierto necesita historial de confirmaciones. Para la demo, reglas en código y, a lo mucho, vista de solo lectura. |
| P06 Detalle de tipo de componente | Primero como el clic dentro de Confiabilidad, sin histograma. |
| P21 Causas de falla | La lista se carga desde `inicializar` y se edita en el panel de Django. |
| Vistas guardadas, exportaciones en segundo plano | Después. |

Resultado: de 31 pantallas quedan 29; de esas, unas 7 nuevas o con cambios fuertes forman el núcleo de la demo.

## 5. Lo que realmente cuesta: la capa de datos

El trabajo pesado no son las pantallas, sino lo que va debajo y hoy no existe:

1. Modelos nuevos: tipo de componente, componente instalado, falla, causa y, posiblemente, modelo de autobús y región de la unidad.
2. Eventos y contadores que se actualizan al recibir cada lote (arranque, uso, encendido, fuga → sumar ciclos u horas al componente instalado).
3. Resumen por hora y depuración de lecturas crudas de más de 72 h. Puede dejarse al final de la demo.

Cada cambio de modelo se revisa con el equipo antes de implementarse.

## 6. Hardware: no hace falta cambiarlo

Todo lo que se quiere contar se deduce en el servidor de las 13 señales actuales:

| Componente | Cómo se cuenta | Hardware nuevo |
|---|---|---|
| Ventilador del A/C (arranques y horas) | `ac.ventilador_rpm` cruza 300 rpm | No |
| Bomba de descarga y guillotina | Cada uso del sanitario (`san.ocupado` / `san.usos`) = un ciclo **estimado** | No |
| Llenados del tanque | Subidas de `san.tanque_nivel` | No |
| Sellos / fugas | `san.fuga` y `san.humedad_piso` | No |
| Lámparas (encendidos y horas) | `luz.interior` cruza un umbral | No |
| Alimentación y conexiones | Caídas de `elec.voltaje` | No |

Ajustes recomendados:

1. **Reinicio de `san.usos`**: resolverlo en el servidor (sumar diferencias). Opcional: guardar el contador en la memoria flash del ESP32 (unas cuantas líneas de firmware).
2. **Iluminación aproximada**: el LDR mide luz ambiente; de día puede confundir la lámpara con el sol. Se refleja en el nivel de certeza.
3. **Opcional, para pasar de «estimado» a «medido»**: un fin de carrera o reed switch en la guillotina (entrada digital) y un sensor de corriente tipo ACS712 en la alimentación de la bomba. Serían una o dos señales nuevas en la trama más un cambio de firmware. No se recomienda para la demo.

Números de serie, modelo de la unidad y fecha de fábrica son datos que se capturan, no sensores.

## 7. Orden de trabajo sugerido

1. Modelos de componente, falla y causa, con datos de ejemplo cargados por `inicializar`.
2. Contadores: al recibir cada lote, sumar ciclos y horas al componente instalado.
3. Ficha de la unidad con la pestaña Componentes, y la pantalla Componente instalado.
4. Orden de servicio que registra causa y componentes, y crea la falla (con marca de prematura).
5. Confiabilidad y Panorama leyendo esas fallas y contadores.
6. Después: Alertas con «tomar» y diagnóstico, Calidad de los datos, Señales y umbrales, división Unidades / Dispositivos.

## 8. Estado de la implementación

Implementado (app nueva `confiabilidad` y cambios en `tablero`, `servicio` y `telemetria`):

| Pantalla | Ruta | Notas |
|---|---|---|
| P04 Panorama | `/panorama/` | Pantalla de inicio de Ingeniería. Filtros por operador, región, modelo y periodo; gráfica semanal y mapa de calor. La vista anterior por operador sigue en `/flota/`. |
| P05 Confiabilidad | `/confiabilidad/` | Vida a la falla contra garantía, reparto de fallas, Pareto de causas y comparación por región, operador, modelo o fabricante. |
| P06 Detalle de tipo de componente | `/confiabilidad/tipos/<id>/` | Histograma de vida, causas, dónde falla, reincidencias y lista de fallas. |
| P08 Calidad de los datos | `/calidad/` | Sensores con menos de 95 % de lecturas válidas (con «Crear orden»), unidades sin reportar, gateways y nodos con problemas. |
| P09 Unidades | `/autobuses/` | Tabla paginada (50) con búsqueda por unidad, operador o número de serie y filtro «componente arriba del 80 %». |
| P10 Ficha de la unidad | `/autobuses/<id>/` | Pestañas Resumen, Componentes, En vivo, Fallas y órdenes, Dispositivos (según el rol). |
| P12 Componente instalado | `/componentes/<id>/` | Barra de vida, falla, historial de la posición y corrección del contador con motivo (queda en la bitácora). |
| P13 Alertas | `/alertas/` | «Tomar» / «Soltar», pestaña «Mis pendientes», diagnóstico probable con certeza, fallas que se repiten, filtros de región, subsistema y quién la trabaja. |
| P14–P16 Órdenes | `/ordenes/` | Componente afectado, asignación a técnico de convenio, confirmar o rechazar el diagnóstico, causa del catálogo, cambio de pieza con número de serie y marca de falla prematura. |
| P20 Catálogo de componentes | `/catalogo/` | Alta y edición; un tipo nuevo se instala en todas las unidades. |
| P23 Señales y umbrales | `/senales/` | Rangos y umbrales con valor anterior y nuevo en la bitácora. |
| P24 Dispositivos | `/dispositivos/` | Filtros de clave de más de 90 días, sin contacto 24 h y revocados. |

- **Conteo de ciclos y horas**: `confiabilidad/conteo.py`, se ejecuta con cada lote recibido.
- **Diagnóstico probable**: `confiabilidad/diagnostico.py`, reglas fijas por señal y tipo de alerta.
- **Rol «Técnico de convenio»**: solo ve sus órdenes asignadas y esas unidades; lo que registra queda «Por validar por Ingeniería» y la falla cuenta en los análisis hasta que se aprueba.
- **Inicio por rol**: Ingeniería → Panorama, Administrador → Usuarios, técnicos → «Mis pendientes».
- **Datos de ejemplo** (`python manage.py inicializar`): 5 tipos de componente, 16 causas, región y modelo de las unidades demo y 37 fallas del último año. La historia que cuentan: la guillotina falla antes de tiempo en las regiones de clima cálido húmedo.

Sigue pospuesto: P07 Comportamiento de variables, P17/P18 Investigaciones, P22 Reglas de diagnóstico, resumen por hora y depuración de lecturas de 72 h, vistas guardadas y exportaciones en segundo plano.
