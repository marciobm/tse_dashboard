# TSE Results Dashboard

Dashboard local para acompanhar resultados de Presidente/Brasil das Eleições 2026.

## 1. Instalação

```bash
python -m venv .venv
```

### Windows
```bash
.venv\Scripts\activate
```

### macOS/Linux
```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## 2. Executar

```bash
uvicorn app:app --reload
```

Abra:

http://127.0.0.1:8000

API:

http://127.0.0.1:8000/resultados

Documentação automática:

http://127.0.0.1:8000/docs

## 3. Endpoints

- GET /resultados
- GET /resultados/top?limit=10
- GET /resultados/candidato/{numero}
- GET /health

## 4. Atualização

O worker consulta o JSON oficial do TSE a cada 60 segundos e usa ETag/Last-Modified.
Quando o TSE responde 304, o banco não é alterado.

## 5. Alterar cargo/abrangência

Altere TSE_URL em app.py conforme o arquivo oficial desejado.
