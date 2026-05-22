# PROJECT_PLAN.md — Plan de construcción por fases

Plan para construir el dashboard con Claude Code. Avanza **una fase a la vez** y haz
`commit` cada vez que algo funcione. Marca las casillas a medida que completas.

Para cada fase hay un **prompt sugerido** que puedes pegar directamente en Claude Code.

---

## Fase 0 — Preparar el terreno

**Objetivo:** dejar listo el entorno antes de escribir lógica.

- [ ] Instalar Claude Code (instalador nativo, o `npm install -g @anthropic-ai/claude-code`
      con Node.js 18+). Verificar con `claude doctor`.
- [ ] Crear repo vacío en GitHub y clonarlo en local.
- [ ] Copiar `CLAUDE.md` y `PROJECT_PLAN.md` a la raíz del repo.
- [ ] Crear entorno virtual de Python (`python -m venv venv`).
- [ ] Crear `.gitignore` (que ignore `.env`, `venv/`, `__pycache__/`).
- [ ] Crear `.env.example` (plantilla) y `.env` real (con credenciales, NO se sube a git).
- [ ] Crear `requirements.txt`.
- [ ] **Ajustar el modelo de datos en `CLAUDE.md`** con los nombres reales de tu tabla.
- [ ] Primer commit + push.

**Prompt sugerido para Claude Code:**
> Lee CLAUDE.md. Crea la estructura inicial del proyecto: `.gitignore` (ignorando .env, venv
> y caches de Python), `.env.example` con las variables que aparecen en CLAUDE.md pero sin
> valores, `requirements.txt` con streamlit, pandas, facebook-business, supabase, python-dotenv
> y plotly, y un README breve. No escribas todavía lógica de negocio.

---

## Fase 1 — Conectar cada fuente por separado (solo lectura)

**Objetivo:** confirmar que podemos leer datos de cada fuente. **Todavía no se unen.**

- [ ] `src/facebook_client.py`: trae por rango de fechas `ad_id`, nombre del anuncio y gasto.
- [ ] `src/supabase_client.py`: lee la tabla de ventas (`ad_id`, monto, fecha).
- [ ] Probar cada script de forma aislada e imprimir resultados.
- [ ] Validar que el gasto coincida con el Administrador de Anuncios de Facebook.
- [ ] Commit.

**Prompt sugerido para Claude Code:**
> Crea `src/facebook_client.py` con una función que, dado un rango de fechas, use la librería
> facebook-business para traer los insights a nivel de anuncio: ad_id, ad_name y spend. Lee las
> credenciales desde .env. Agrega un bloque `if __name__ == "__main__"` que imprima los
> resultados de los últimos 7 días para probarlo. Luego crea `src/supabase_client.py` que lea
> la tabla de ventas (nombre y columnas en CLAUDE.md) y devuelva un DataFrame. NO unas todavía
> las dos fuentes.

---

## Fase 2 — El cruce (corazón del proyecto)

**Objetivo:** unir ambas fuentes por `ad_id` y calcular las métricas.

- [ ] `src/metrics.py`: une Facebook + Supabase por `ad_id` con pandas.
- [ ] Calcular por anuncio: nº de ventas, monto total, ROAS (ventas/gasto), CPA (gasto/ventas).
- [ ] Manejar casos borde (anuncios con gasto y sin ventas, y viceversa).
- [ ] **Validar a mano** los números de 2–3 anuncios reales.
- [ ] Commit.

**Prompt sugerido para Claude Code:**
> Crea `src/metrics.py` con una función que reciba un rango de fechas, use facebook_client y
> supabase_client, y una ambas fuentes por ad_id con pandas. Debe agrupar las ventas por ad_id
> (conteo y suma del monto), unirlas con el gasto y el nombre del anuncio, y calcular ROAS
> (monto_ventas / gasto) y CPA (gasto / nº_ventas). Maneja divisiones por cero. Devuelve un
> DataFrame ordenado por monto de ventas descendente.

---

## Fase 3 — Dashboard en Streamlit

**Objetivo:** la interfaz que vas a usar día a día.

- [ ] `app.py`: filtro de fechas, KPIs arriba (gasto total, ventas totales, ROAS global).
- [ ] Tabla por anuncio ordenable por ventas o ROAS, mostrando el **nombre** del anuncio.
- [ ] Gráficas: gasto vs. ventas, y ranking de anuncios que más venden.
- [ ] Cachear las llamadas a Facebook con `st.cache_data`.
- [ ] Commit.

**Prompt sugerido para Claude Code:**
> Crea `app.py` con Streamlit usando src/metrics.py. Incluye: un selector de rango de fechas en
> la barra lateral; una fila de KPIs (gasto total, monto de ventas, ROAS global); una tabla por
> anuncio con su nombre, gasto, nº de ventas, monto, ROAS y CPA, ordenable; y dos gráficas con
> plotly (gasto vs ventas por anuncio, y top anuncios por ventas). Envuelve la carga de datos de
> Facebook en una función con @st.cache_data.

---

## Fase 4 — GitHub y despliegue en el VPS

**Objetivo:** dejar la app corriendo de forma permanente y privada en tu VPS.

- [ ] Push de todo a GitHub (sin secretos).
- [ ] En el VPS: clonar repo, crear venv, instalar dependencias.
- [ ] Crear el `.env` directamente en el VPS (no pasa por git).
- [ ] Dejar la app corriendo con un servicio de systemd.
- [ ] Nginx como proxy inverso + protección con contraseña (basic auth).
- [ ] Opcional: dominio + certificado SSL (certbot).
- [ ] Probar acceso desde el navegador.

**Prompt sugerido para Claude Code (ejecutándolo por SSH en el VPS):**
> Estoy en mi VPS (Ubuntu). Ayúdame a desplegar esta app de Streamlit: crea un servicio de
> systemd que la mantenga corriendo, configura Nginx como proxy inverso hacia el puerto de
> Streamlit con autenticación básica por contraseña, y dame los pasos para añadir SSL con
> certbot. El .env ya lo creé manualmente en el servidor.

> Nota de seguridad: la creación de cuentas, contraseñas y certificados conviene hacerla tú
> mismo siguiendo los pasos; Claude Code te guía pero tú ejecutas lo sensible.

---

## Fase 5 — Iterar

Ideas para después de tener la base funcionando:

- [ ] Refresco automático de datos (cron / scheduler).
- [ ] Métricas extra: tendencia por día, comparativa entre periodos.
- [ ] Alertas (p. ej. anuncio con CPA por encima de un umbral).
- [ ] Soporte para varias cuentas publicitarias.
- [ ] Desglose por campaña / conjunto de anuncios además de por anuncio.

---

## Recordatorios

- Una fase a la vez. Commit cuando funcione.
- Credenciales solo en `.env`, nunca en git.
- Validar números contra Facebook antes de cerrar cada fase.
- Pide pruebas para la Fase 2 (es donde más importa que los números cuadren).
