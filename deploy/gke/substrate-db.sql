-- Substrate's roles, schema and privileges, as in the Cloud SQL guide of substrate-gke. Safe to run again.
-- Run as substrate_admin in the substrate database: psql -v ON_ERROR_STOP=1 -v iam_user=<IAM database user> -f ...
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'substrate_owner') THEN
    CREATE ROLE substrate_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'substrate_readwrite') THEN
    CREATE ROLE substrate_readwrite NOLOGIN;
  END IF;
END
$$;

-- Creating a role grants its creator no right to use it. This one is needed to own the schema and its default
-- privileges.
GRANT substrate_owner TO CURRENT_USER WITH SET TRUE;
GRANT substrate_owner, substrate_readwrite TO :"iam_user";

CREATE SCHEMA IF NOT EXISTS substrate AUTHORIZATION substrate_owner;
GRANT USAGE ON SCHEMA substrate TO substrate_readwrite;
ALTER DEFAULT PRIVILEGES FOR ROLE substrate_owner IN SCHEMA substrate
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO substrate_readwrite;
ALTER DEFAULT PRIVILEGES FOR ROLE substrate_owner IN SCHEMA substrate
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO substrate_readwrite;
ALTER DEFAULT PRIVILEGES FOR ROLE substrate_owner IN SCHEMA substrate
  GRANT EXECUTE ON ROUTINES TO substrate_readwrite;
ALTER DEFAULT PRIVILEGES FOR ROLE substrate_owner IN SCHEMA substrate
  GRANT USAGE ON TYPES TO substrate_readwrite;

COMMIT;
