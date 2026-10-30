# Trabalho SDI

Para a turma 2026/2

## Cadastro

Componente **Cadastro** do sistema distribuído. Expõe um painel web para gerar/enviar `NEWUSER_*` e `KILL_*` ao **coordenador**, mantém as listas de usuários e servidores, e recebe comandos de controle (`NEWID_*` / `NEWSERVERIP_*`).

---

## Requisitos

- Python **3.9+** (apenas biblioteca padrão — sem `pip install`).
- Navegador moderno para o painel.

---

## Configuração (`.env`)

O arquivo `.env` deve ficar na **pasta pai** de `cadastro.py`(`BASE_DIR/.env`). Variáveis de ambiente do processo têm prioridade sobre o `.env`.

### `.env` de exemplo

```env
SERVER_IP=127.0.0.1
SERVER_PORT=5001
ENCODING=utf-8

CONTROL_PORT=6001

HTTP_PORT=8000
HTTP_HOST=0.0.0.0
```
