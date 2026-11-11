#!/bin/sh
# Wird im Backup-Container ausgeführt: tägliche Sicherung der ERP-Datenbank.
# Wiederherstellen: pg_restore -h <server> -U erp -d erp --clean /backup/erp_JJJJ-MM-TT.dump
while true; do
  stamp=$(date +%F)
  pg_dump -Fc erp > "/backup/erp_${stamp}.dump" && echo "Sicherung erp_${stamp}.dump erstellt"
  # Sicherungen älter als 14 Tage entfernen (nur Dateien erp_*.dump in /backup)
  find /backup -maxdepth 1 -type f -name 'erp_*.dump' -mtime +14 -exec rm -f {} \;
  sleep 86400
done
