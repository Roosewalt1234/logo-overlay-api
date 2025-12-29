# Logo Overlay API

A production-ready Python Flask API that overlays a downloaded logo onto a base image (sent as base64) and returns the final image as base64 PNG.

## Run locally

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

API listens on `http://localhost:5000`.

## API

### GET /health

Returns:

```json
{"status":"healthy"}
```

### POST /overlay-logo

Request JSON:

- `base_image` (required): base64-encoded image string (PNG/JPG). Data-URL format is also accepted.
- `logo_url` (required): http(s) URL to a logo image.
- `logo_scale` (optional, default `0.15`): logo width as a fraction of base image width.
- `position` (optional, default `top-right`): `top-right`, `top-left`, `bottom-right`, `bottom-left`
- `padding` (optional, default `20`): padding from edges in pixels.

Example (replace `BASE64_HERE`):

```bash
curl -X POST http://localhost:5000/overlay-logo -H "Content-Type: application/json" -d "{\"base_image\":\"BASE64_HERE\",\"logo_url\":\"https://example.com/logo.png\",\"logo_scale\":0.15,\"position\":\"top-right\",\"padding\":20}"
```

Success response:

```json
{"status":"success","image":"...","format":"base64"}
```

## Deploy to Railway

- Push the project to GitHub.
- Create a new Railway project and connect the repo.
- Set the start command to:

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

Railway sets `$PORT` automatically.