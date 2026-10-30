"""Backup diário do SQLite via API .backup() (seguro mesmo com DB em uso).

Uso local:
    python backup.py

Uso em produção (Render Cron Job):
    Command: python backup.py
    Schedule: 0 3 * * *   (todo dia às 3h UTC)

Variáveis:
    DATA_DIR      — diretório do DB (default: /data se existir, senão ".")
    BACKUP_KEEP   — quantos backups manter (default: 7)
    DATABASE_URL  — se definido (Postgres), este script não faz nada (use pg_dump)
"""
import os
import sys
import sqlite3
import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("friday.backup")

DATA_DIR = os.getenv("DATA_DIR", "/data" if os.path.exists("/data") and os.access("/data", os.W_OK) else ".")
DB_FILE = os.path.join(DATA_DIR, "friday.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
KEEP = int(os.getenv("BACKUP_KEEP", "7"))


def main():
    if os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        logger.info("DATABASE_URL é Postgres — use pg_dump. Nada a fazer aqui.")
        return 0

    if not os.path.exists(DB_FILE):
        logger.warning(f"DB não encontrado: {DB_FILE}")
        return 1

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"friday_{stamp}.db")

    # .backup() do SQLite é a forma correta (não usa shutil.copy)
    src = sqlite3.connect(DB_FILE)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
        logger.info(f"Backup criado: {dst}")
    finally:
        out.close()
        src.close()

    backups = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("friday_") and f.endswith(".db")
    )
    for old in backups[:-KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
            logger.info(f"Removido (rotacionado): {old}")
        except Exception as e:
            logger.warning(f"Falha ao remover {old}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
