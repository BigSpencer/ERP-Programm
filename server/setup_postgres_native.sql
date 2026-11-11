-- Alternative ohne Docker: auf einem vorhandenen PostgreSQL-Server als Superuser ausführen
--   psql -U postgres -f setup_postgres_native.sql
CREATE USER erp WITH PASSWORD 'bitte-aendern';
CREATE DATABASE erp OWNER erp ENCODING 'UTF8';
-- Zusätzlich in pg_hba.conf das Firmennetz freigeben, z. B.:
--   host  erp  erp  192.168.0.0/24  scram-sha-256
-- und in postgresql.conf:  listen_addresses = '*'
