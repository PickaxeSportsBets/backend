

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE EXTENSION IF NOT EXISTS "pg_cron" WITH SCHEMA "pg_catalog";






CREATE EXTENSION IF NOT EXISTS "pg_net" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgsodium" WITH SCHEMA "pgsodium";






COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "http" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";






CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgjwt" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE OR REPLACE FUNCTION "public"."check_arbitrage_api"() RETURNS "jsonb"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_response http_response;
    v_data jsonb;
BEGIN
    SELECT status, content::jsonb 
    INTO v_response.status, v_data
    FROM http_get('https://backend-production-c8b0.up.railway.app/api/v1/db/arbitrage');
    
    RETURN v_data;
END;
$$;


ALTER FUNCTION "public"."check_arbitrage_api"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."process_arbitrage_items"() RETURNS "void"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_response http_response;
    v_data jsonb;
    v_item jsonb;
BEGIN
    -- Set both timeouts
    SET LOCAL statement_timeout = '300s';
    
    -- Get the HTTP response with explicit http timeout params
    SELECT 
        status, content::jsonb 
    INTO 
        v_response.status, v_data
    FROM 
        http_get('https://backend-production-c8b0.up.railway.app/api/v1/db/arbitrage');
    
    -- Start a transaction
    BEGIN
        -- First delete all existing records
        DELETE FROM arbitrage;
        
        -- Then process and insert new records
        FOR v_item IN SELECT * FROM jsonb_array_elements(v_data)
        LOOP
            -- Insert each item directly from JSON
            INSERT INTO arbitrage 
            SELECT * FROM jsonb_populate_record(null::arbitrage, v_item);
        END LOOP;
        
    EXCEPTION WHEN OTHERS THEN
        -- If anything goes wrong, the whole transaction will be rolled back
        RAISE;
    END;
END;
$$;


ALTER FUNCTION "public"."process_arbitrage_items"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."sync_arbitrage_data"() RETURNS "text"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_response http_response;
    v_data jsonb;
    v_count integer;
BEGIN
    -- Set statement timeout to 5 minutes
    SET LOCAL statement_timeout = '300s';
    
    -- Fetch data from API
    SELECT status, content::jsonb 
    INTO v_response.status, v_data
    FROM http_get('https://backend-production-c8b0.up.railway.app/api/v1/db/arbitrage');
    
    -- Check if request was successful
    IF v_response.status != 200 THEN
        RAISE EXCEPTION 'API request failed with status %', v_response.status;
    END IF;
    
    -- Start transaction
    BEGIN
        -- Delete existing data
        DELETE FROM arbitrage;
        
        -- Insert new data
        WITH inserted AS (
            INSERT INTO arbitrage 
            SELECT * FROM jsonb_populate_recordset(null::arbitrage, v_data)
            RETURNING *
        )
        SELECT count(*) INTO v_count FROM inserted;
        
        -- Return success message
        RETURN format('Successfully synced %s arbitrage records', v_count);
    EXCEPTION WHEN OTHERS THEN
        -- Rollback will happen automatically
        RAISE EXCEPTION 'Failed to sync arbitrage data: %', SQLERRM;
    END;
END;
$$;


ALTER FUNCTION "public"."sync_arbitrage_data"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_timestamp"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$BEGIN
  NEW.timestamp := CURRENT_TIMESTAMP;
  RETURN NEW;
END$$;


ALTER FUNCTION "public"."update_timestamp"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_updated_at_column"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
    new.updated_at = now();
    return new;
end;
$$;


ALTER FUNCTION "public"."update_updated_at_column"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."arbitrage" (
    "opportunity_type" "text",
    "hold_percentage" double precision,
    "sport" "text",
    "market_type" "text",
    "prop_description" "text",
    "market_point" "text",
    "game" "text",
    "commence_time" timestamp with time zone,
    "team1_name" "text",
    "team1_book" "text",
    "team1_odds" bigint,
    "team1_point" "text",
    "team1_stake" double precision,
    "team1_link" "text",
    "team2_name" "text",
    "team2_book" "text",
    "team2_odds" bigint,
    "team2_point" "text",
    "team2_stake" double precision,
    "team2_link" "text",
    "profit_percentage" "text",
    "timestamp" timestamp with time zone,
    "primary_key" "text" NOT NULL
);


ALTER TABLE "public"."arbitrage" OWNER TO "postgres";


COMMENT ON TABLE "public"."arbitrage" IS 'Arbitrage opportunities';



COMMENT ON COLUMN "public"."arbitrage"."commence_time" IS 'UTC Time';



COMMENT ON COLUMN "public"."arbitrage"."timestamp" IS 'UTC time';



CREATE TABLE IF NOT EXISTS "public"."ev_graph" (
    "sport" "text",
    "market_type" "text",
    "market_point" double precision,
    "game" "text",
    "commence_time" timestamp with time zone,
    "team" "text",
    "bookmaker" "text",
    "odds" bigint,
    "fair_odds" bigint,
    "ev_percentage" double precision,
    "link" "text",
    "market_data" "jsonb",
    "primary_key" "text" NOT NULL,
    "timestamp" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."ev_graph" OWNER TO "postgres";


COMMENT ON TABLE "public"."ev_graph" IS 'This is a duplicate of plus_ev';



CREATE TABLE IF NOT EXISTS "public"."free_bet" (
    "clerk_id" bigint NOT NULL,
    "lastRedemptionDate" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."free_bet" OWNER TO "postgres";


ALTER TABLE "public"."free_bet" ALTER COLUMN "clerk_id" ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME "public"."free_bet_clerk_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE TABLE IF NOT EXISTS "public"."plus_ev" (
    "sport" "text",
    "market_type" "text",
    "market_point" double precision DEFAULT '0'::double precision,
    "game" "text",
    "commence_time" timestamp with time zone,
    "team" "text",
    "bookmaker" "text",
    "odds" bigint DEFAULT '0'::bigint,
    "fair_odds" bigint DEFAULT '0'::bigint,
    "ev_percentage" double precision DEFAULT '0'::double precision,
    "link" "text",
    "market_data" "jsonb",
    "primary_key" "text" NOT NULL,
    "timestamp" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."plus_ev" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."subscriptions" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "subscription_id" "text" NOT NULL,
    "customer_id" "text" NOT NULL,
    "clerk_user_id" "text" NOT NULL,
    "status" "text" NOT NULL,
    "current_period_start" timestamp with time zone,
    "current_period_end" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."subscriptions" OWNER TO "postgres";


ALTER TABLE ONLY "public"."arbitrage"
    ADD CONSTRAINT "arbitrage_pkey" PRIMARY KEY ("primary_key");



ALTER TABLE ONLY "public"."arbitrage"
    ADD CONSTRAINT "arbitrage_primary_key_key" UNIQUE ("primary_key");



ALTER TABLE ONLY "public"."free_bet"
    ADD CONSTRAINT "free_bet_pkey" PRIMARY KEY ("clerk_id");



ALTER TABLE ONLY "public"."plus_ev"
    ADD CONSTRAINT "plus_ev_pkey" PRIMARY KEY ("primary_key");



ALTER TABLE ONLY "public"."subscriptions"
    ADD CONSTRAINT "subscriptions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."subscriptions"
    ADD CONSTRAINT "subscriptions_subscription_id_key" UNIQUE ("subscription_id");



CREATE OR REPLACE TRIGGER "update_timestamp_plus_ev" AFTER UPDATE ON "public"."plus_ev" FOR EACH ROW EXECUTE FUNCTION "public"."update_timestamp"();



CREATE POLICY "anon" ON "public"."arbitrage" TO "anon" USING (true) WITH CHECK (true);



CREATE POLICY "anon" ON "public"."ev_graph" TO "anon" USING (true) WITH CHECK (true);



CREATE POLICY "anon" ON "public"."free_bet" TO "anon" USING (true) WITH CHECK (true);



CREATE POLICY "anon" ON "public"."plus_ev" TO "anon" USING (true) WITH CHECK (true);



CREATE POLICY "anon" ON "public"."subscriptions" TO "authenticated", "anon", "authenticator" USING (true) WITH CHECK (true);



ALTER TABLE "public"."arbitrage" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."ev_graph" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."free_bet" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."plus_ev" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."subscriptions" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";








GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";








































































































































































































































































GRANT ALL ON FUNCTION "public"."check_arbitrage_api"() TO "anon";
GRANT ALL ON FUNCTION "public"."check_arbitrage_api"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."check_arbitrage_api"() TO "service_role";



GRANT ALL ON FUNCTION "public"."process_arbitrage_items"() TO "anon";
GRANT ALL ON FUNCTION "public"."process_arbitrage_items"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."process_arbitrage_items"() TO "service_role";



GRANT ALL ON FUNCTION "public"."sync_arbitrage_data"() TO "anon";
GRANT ALL ON FUNCTION "public"."sync_arbitrage_data"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."sync_arbitrage_data"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_timestamp"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_timestamp"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_timestamp"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "service_role";
























GRANT ALL ON TABLE "public"."arbitrage" TO "anon";
GRANT ALL ON TABLE "public"."arbitrage" TO "authenticated";
GRANT ALL ON TABLE "public"."arbitrage" TO "service_role";



GRANT ALL ON TABLE "public"."ev_graph" TO "anon";
GRANT ALL ON TABLE "public"."ev_graph" TO "authenticated";
GRANT ALL ON TABLE "public"."ev_graph" TO "service_role";



GRANT ALL ON TABLE "public"."free_bet" TO "anon";
GRANT ALL ON TABLE "public"."free_bet" TO "authenticated";
GRANT ALL ON TABLE "public"."free_bet" TO "service_role";



GRANT ALL ON SEQUENCE "public"."free_bet_clerk_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."free_bet_clerk_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."free_bet_clerk_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."plus_ev" TO "anon";
GRANT ALL ON TABLE "public"."plus_ev" TO "authenticated";
GRANT ALL ON TABLE "public"."plus_ev" TO "service_role";



GRANT ALL ON TABLE "public"."subscriptions" TO "anon";
GRANT ALL ON TABLE "public"."subscriptions" TO "authenticated";
GRANT ALL ON TABLE "public"."subscriptions" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "service_role";






























RESET ALL;
