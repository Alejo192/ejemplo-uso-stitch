# EcoGroove · landing + tienda sobre el backend de Grafikte

Landing de **EcoGroove** (audio-ecología + ODS 13) que vende su línea de
merchandising ecológico (camiseta, mug, libreta, calendario) usando el mismo
backend de pedidos de **Grafikte Publicidad** (`app.py`), con un visor 3D
por producto, subida de diseño propio y envío del pedido directo a la base
de datos y a WhatsApp.

## Qué se agregó sobre el `app.py` original
- `precios.json` ahora incluye una sección `"merch"` con el precio unitario
  de camiseta, mug, libreta y calendario.
- `calcular_precio()` en `app.py` ganó una rama para esos 4 productos:
  precio = precio unitario × cantidad (`opciones.cant`, 1 a 500 unidades).
  El resto del backend (validaciones, subida de archivos, WhatsApp, panel
  `/admin`, límite de 10 pedidos/hora) se dejó intacto.
- `static/index.html`: la landing completa.
- `static/politica.html`: política de datos base (edítala con tu abogado
  antes de publicar; luego actualiza `POLITICA_VERSION` en `app.py`).

## Cómo correrlo

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # y completa ADMIN_PASS, WHATSAPP, etc.
python app.py
```

Abre `http://127.0.0.1:5000`. El panel de pedidos queda en
`http://127.0.0.1:5000/admin` (usuario/clave de tu `.env`).

## Qué hace la página
- **Reproductor acústico** simulado (visualizador animado, scrubber,
  vinilo girando) — sin streaming externo, por eso nunca falla por CORS.
- **Tracker ODS 13**: contadores que suben en vivo (horas de audio, CO₂
  mitigado) + calculadora de impacto personal con slider.
- **Sello QR de verificación**: genera un QR por lote (librería `qrcode.js`
  vía CDN) y muestra un certificado simulado de trazabilidad.
- **Comunidad de compromisos**: formulario que guarda firmas en
  `localStorage` del navegador (no toca el backend; es solo del lado
  cliente, tal como lo pedía el plan original).
- **Tienda Verde**: por cada producto hay un visor 3D hecho con
  `three.js` (puedes arrastrar para girar la pieza), un selector de
  cantidad y un campo opcional para subir tu propio diseño — ese archivo
  se textura sobre el modelo 3D en vivo. Al pulsar "Pedir este producto"
  se abre un modal que pide nombre, teléfono, correo y la autorización de
  datos, y al enviarlo hace `POST /api/pedido` igual que cualquier pedido
  de Grafikte: queda en `pedidos.db`, el archivo se guarda renombrado en
  `uploads/<id>/`, y si configuraste `WHATSAPP` en el `.env` se te ofrece
  un enlace para coordinar la entrega.

## Pendientes antes de producción
1. **Política de datos**: revisa `static/politica.html` con un abogado y
   actualiza `POLITICA_VERSION` en `app.py`.
2. **Precios**: ajusta `precios.json` (impresos y la sección `merch`) a tus
   costos reales.
3. **WhatsApp / correo**: completa `.env` con tu número y, si quieres
   notificación por correo de cada pedido, tus credenciales SMTP.
4. **Clave de admin**: define un `ADMIN_PASS` fuerte antes de exponer el
   sitio a internet.
5. Los CDN usados (jsDelivr para three.js/qrcode, Google Fonts) deben ser
   accesibles desde donde despliegues el sitio; si tu hosting bloquea
   CDNs externos, descarga esos archivos y sírvelos desde `static/`.
